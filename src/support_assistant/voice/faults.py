"""Runtime fault injection for the demo: wrappers around real providers, flipped by a shared
FaultInjector. The providers themselves stay clean; only these wrappers know about faults.

Targets: a provider name ("inhouse", "edge", "groq-whisper", "local-whisper") or a kind
("stt", "tts") for every provider of that kind. A provider-specific fault wins over a kind one.

Modes:
  refuse                 immediate retryable connection error (a killed network)
  invalid_key            immediate non-retryable auth error
  hang                   never returns; only the chain's timeouts can end it
  drop_after_chunks:N    TTS: yield N chunks then fail mid-stream
  slow:MS                add MS of latency (before the first result / chunk)
  unreachable            TTS in-house only: really dial a dead address (a genuine network failure)
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from support_assistant.config import Settings, get_settings
from support_assistant.voice.contracts import (
    STT,
    TTS,
    CallEvent,
    EventSink,
    STTError,
    Transcript,
    TTSError,
)
from support_assistant.voice.tts import InHouseStreamTTS

MODES = {"refuse", "invalid_key", "hang", "drop_after_chunks", "slow", "unreachable"}
_POSITIONAL = {"drop_after_chunks": "n", "slow": "ms"}
UNREACHABLE_URL = "ws://127.0.0.1:9/v1/tts/stream"  # discard port: nothing listens, refused at once
KINDS = {"stt", "tts"}
PROVIDERS = {"inhouse": "tts", "edge": "tts", "groq-whisper": "stt", "local-whisper": "stt"}


@dataclass(frozen=True)
class Fault:
    mode: str
    params: dict[str, Any] = field(default_factory=dict)


class FaultInjector:
    def __init__(self, events: EventSink | None = None, clock: Callable[[], float] | None = None):
        self._faults: dict[str, Fault] = {}
        self._events = events
        t0 = time.monotonic()
        self._clock = clock or (lambda: (time.monotonic() - t0) * 1000)

    def set(self, target: str, mode: str, **params: Any) -> None:
        """`mode` may carry its argument inline: "drop_after_chunks:2", "slow:800"."""
        base, _, arg = mode.partition(":")
        if base not in MODES:
            raise ValueError(f"unknown fault mode {mode!r}; one of {sorted(MODES)}")
        if target not in KINDS and target not in PROVIDERS:
            raise ValueError(f"unknown fault target {target!r}")
        if arg:
            params[_POSITIONAL[base]] = float(arg) if base == "slow" else int(arg)
        if base == "drop_after_chunks":
            params.setdefault("n", 1)
        if base == "slow":
            params.setdefault("ms", 1000)
        self._faults[target] = Fault(base, params)
        self._emit("fault_injected", target=target, mode=base, **params)

    def clear(self, target: str | None = None) -> None:
        """Clear one target, or everything when `target` is None."""
        cleared = list(self._faults) if target is None else [target]
        for t in cleared:
            self._faults.pop(t, None)
        self._emit("fault_cleared", target=target or "all")

    def active(self, provider: str, kind: str) -> Fault | None:
        return self._faults.get(provider) or self._faults.get(kind)

    def _emit(self, kind: str, **data: Any) -> None:
        if self._events is not None:
            self._events(CallEvent(t_ms=self._clock(), kind=kind, data=data))


async def _hang() -> None:
    await asyncio.Event().wait()  # cancellable, never set


class FaultySTT:
    def __init__(self, inner: STT, injector: FaultInjector) -> None:
        self._inner = inner
        self._inj = injector
        self.name = inner.name

    async def warm_up(self) -> None:
        fn = getattr(self._inner, "warm_up", None)
        if fn is not None:
            await fn()

    async def transcribe(self, pcm: bytes) -> Transcript:
        f = self._inj.active(self.name, "stt")
        if f is not None:
            if f.mode in ("refuse", "unreachable"):
                raise STTError(self.name, "connection refused (injected)", retryable=True)
            if f.mode == "invalid_key":
                raise STTError(self.name, "401 invalid API key (injected)", retryable=False)
            if f.mode == "hang":
                await _hang()
            if f.mode == "slow":
                await asyncio.sleep(f.params["ms"] / 1000)
        return await self._inner.transcribe(pcm)


class FaultyTTS:
    def __init__(self, inner: TTS, injector: FaultInjector) -> None:
        self._inner = inner
        self._inj = injector
        self.name = inner.name

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        f = self._inj.active(self.name, "tts")  # sampled when the stream starts
        inner: Any = self._inner
        limit: int | None = None
        if f is not None:
            if f.mode == "refuse":
                raise TTSError(self.name, "connection refused (injected)", retryable=True)
            if f.mode == "invalid_key":
                raise TTSError(self.name, "401 invalid API key (injected)", retryable=False)
            if f.mode == "hang":
                await _hang()
            if f.mode == "slow":
                await asyncio.sleep(f.params["ms"] / 1000)
            if f.mode == "unreachable" and hasattr(inner, "with_url"):
                inner = inner.with_url(UNREACHABLE_URL)
            if f.mode == "drop_after_chunks":
                limit = int(f.params["n"])
        sent = 0
        agen = inner.stream(text)
        try:
            async for chunk in agen:
                if limit is not None and sent >= limit:
                    raise TTSError(
                        self.name, f"stream dropped after {sent} chunks (injected)", retryable=True
                    )
                sent += 1
                yield chunk
        finally:
            await agen.aclose()
        if limit is not None and sent <= limit:
            # provider finished with <= N chunks: still honour the drop so the demo is deterministic
            raise TTSError(
                self.name, f"stream dropped after {sent} chunks (injected)", retryable=True
            )


def unreachable_inhouse(settings: Settings | None = None) -> InHouseStreamTTS:
    """A real in-house TTS client aimed at a dead address: a genuine connection failure (the OS
    refuses the TCP connect) rather than a simulated exception."""
    return InHouseStreamTTS(settings or get_settings(), url=UNREACHABLE_URL, open_timeout_s=2)
