"""Provider chains: ordered fallback with per-attempt timeouts, retries and circuit breakers.

Nothing here can hang a turn: every attempt runs under its own `asyncio.timeout`, so a provider
that never answers costs at most one timeout, and once its breaker opens it costs nothing.

TTS mid-stream failure policy (deliberate trade-off): when a provider dies after it already
produced audio, the next provider re-synthesizes the SAME text from the start. The alternative
(resuming mid-sentence) is impossible across providers, since their chunk boundaries and voices
differ. The coordinator sends each reply as one TTS request (one session keeps the voice
continuous), so the cost is that the listener hears the start of that reply again, in the
fallback voice. The `provider_fallback` event carries `restart=True` and `chunks_already_sent`
so the logs say so.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from opentelemetry.trace import Status, StatusCode

from support_assistant.llm.client import CircuitBreaker
from support_assistant.observability import meter, tracer
from support_assistant.voice.contracts import (
    STT,
    TTS,
    CallEvent,
    EventSink,
    ProviderError,
    STTError,
    Transcript,
    TTSError,
)

log = logging.getLogger(__name__)
_tracer = tracer(__name__)
_meter = meter(__name__)
_fallbacks = _meter.create_counter("voice.provider.fallbacks")
_errors = _meter.create_counter("voice.provider.errors")
_skips = _meter.create_counter("voice.provider.circuit_skips")


class _Emitter:
    """Builds CallEvents with a call-relative clock (ms). Never lets a sink bug break a turn."""

    def __init__(self, events: EventSink | None, clock: Callable[[], float] | None) -> None:
        self._events = events
        if clock is None:
            t0 = time.monotonic()
            clock = lambda: (time.monotonic() - t0) * 1000  # noqa: E731
        self._clock = clock

    def now(self) -> float:
        return self._clock()

    def __call__(self, event: str, /, **data: Any) -> None:
        if self._events is None:
            return
        try:
            self._events(CallEvent(t_ms=self._clock(), kind=event, data=data))
        except Exception:  # noqa: BLE001 - observability must not break a call
            log.exception("event sink failed for %s", event)


def _breakers(
    providers: Sequence[Any], threshold: int, cooldown_s: float
) -> dict[str, CircuitBreaker]:
    return {p.name: CircuitBreaker(threshold, cooldown_s) for p in providers}


class FallbackSTT:
    """Tries STT providers in order. Fast retryable errors (429, 5xx, connection) get `retries`
    more tries on the same provider (short backoff); timeouts and non-retryable errors go straight
    to the next one. `timeouts` overrides `timeout_s` per provider name (the last-resort local
    model gets longer, since there is nothing after it)."""

    name = "fallback-stt"

    def __init__(
        self,
        providers: Sequence[STT],
        *,
        timeout_s: float,
        timeouts: Mapping[str, float] | None = None,
        retries: int = 1,
        backoff_s: float = 0.15,
        circuit_failures: int = 3,
        circuit_cooldown_s: float = 30.0,
        events: EventSink | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.providers = list(providers)
        self._timeout_s = timeout_s
        self._timeouts = dict(timeouts or {})
        self._retries = retries
        self._backoff_s = backoff_s
        self._breakers = _breakers(self.providers, circuit_failures, circuit_cooldown_s)
        self._emit = _Emitter(events, clock)

    def breaker(self, name: str) -> CircuitBreaker:
        return self._breakers[name]

    async def warm_up(self) -> None:
        """Warm providers that have a `warm_up` (local whisper model load). Never raises."""
        for p in self.providers:
            fn = getattr(p, "warm_up", None)
            if fn is None:
                continue
            try:
                await fn()
            except Exception:  # noqa: BLE001
                log.exception("warm_up failed for %s", p.name)

    async def transcribe(self, pcm: bytes) -> Transcript:
        attempts: list[str] = []
        last: ProviderError | None = None
        t_start = time.perf_counter()
        with _tracer.start_as_current_span("stt.transcribe") as span:
            span.set_attribute("app.stt.providers", ",".join(p.name for p in self.providers))
            for idx, provider in enumerate(self.providers):
                name = provider.name
                br = self._breakers[name]
                if not br.allow():
                    attempts.append(f"{name}:circuit_open")
                    _skips.add(1, {"kind": "stt", "provider": name})
                    self._emit("provider_skipped_open_circuit", kind="stt", provider=name)
                    continue
                result, err = await self._run_provider(provider, pcm, attempts)
                if result is not None:
                    span.set_attribute("app.stt.provider", name)
                    span.set_attribute("app.stt.fallbacks", idx)
                    total = (time.perf_counter() - t_start) * 1000
                    return Transcript(
                        text=result.text,
                        provider=name,
                        latency_ms=total,
                        language=result.language,
                        attempts=tuple(attempts),
                    )
                last = err
                nxt = self._next_name(idx)
                if nxt:
                    _fallbacks.add(1, {"kind": "stt", "from": name, "to": nxt})
                    self._emit(
                        "provider_fallback",
                        kind="stt",
                        provider=name,
                        to=nxt,
                        error=str(err),
                        retryable=getattr(err, "retryable", None),
                    )
            msg = f"all STT providers failed: {', '.join(attempts) or 'none tried'}"
            exc = STTError(self.name, msg, retryable=False)
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, msg))
            raise exc from last

    def _next_name(self, idx: int) -> str | None:
        return self.providers[idx + 1].name if idx + 1 < len(self.providers) else None

    async def _run_provider(
        self, provider: STT, pcm: bytes, attempts: list[str]
    ) -> tuple[Transcript | None, ProviderError | None]:
        name = provider.name
        br = self._breakers[name]
        err: ProviderError | None = None
        settled = False
        try:
            for n in range(self._retries + 1):
                self._emit("provider_attempt", kind="stt", provider=name, attempt=n + 1)
                t0 = time.perf_counter()
                limit = self._timeouts.get(name, self._timeout_s)
                try:
                    async with asyncio.timeout(limit):
                        out = await provider.transcribe(pcm)
                except TimeoutError:
                    err = STTError(name, f"timed out after {limit:g}s", retryable=True)
                    tag = "timeout"
                except ProviderError as exc:
                    err = exc
                    tag = "retryable_error" if exc.retryable else "error"
                except Exception as exc:  # noqa: BLE001 - never let a bug crash the turn
                    err = STTError(name, f"unexpected {type(exc).__name__}: {exc}", retryable=False)
                    tag = "unexpected_error"
                else:
                    ms = (time.perf_counter() - t0) * 1000
                    attempts.append(f"{name}:ok")
                    br.record(ok=True)
                    settled = True
                    self._emit("provider_ok", kind="stt", provider=name, latency_ms=round(ms, 1))
                    return out, None
                ms = (time.perf_counter() - t0) * 1000
                attempts.append(f"{name}:{tag}")
                _errors.add(1, {"kind": "stt", "provider": name, "error": tag})
                self._emit(
                    "provider_error",
                    kind="stt",
                    provider=name,
                    attempt=n + 1,
                    error=str(err),
                    retryable=err.retryable,
                    latency_ms=round(ms, 1),
                )
                # A timeout is not retried on the same provider: it already cost the full limit,
                # and a hung provider retried once doubles the caller's wait before the fallback.
                if not err.retryable or tag == "timeout" or n == self._retries:
                    break
                await asyncio.sleep(self._backoff_s * (n + 1))
            br.record(ok=False)
            settled = True
            return None, err
        finally:
            if not settled:  # cancelled mid-attempt: release a half-open probe slot
                br.record(ok=False)


class FallbackTTS:
    """Streams from the first healthy provider; see the module docstring for mid-stream policy."""

    name = "fallback-tts"

    def __init__(
        self,
        providers: Sequence[TTS],
        *,
        first_chunk_timeout_s: float,
        chunk_timeout_s: float,
        circuit_failures: int = 3,
        circuit_cooldown_s: float = 30.0,
        events: EventSink | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.providers = list(providers)
        self._first_s = first_chunk_timeout_s
        self._chunk_s = chunk_timeout_s
        self._breakers = _breakers(self.providers, circuit_failures, circuit_cooldown_s)
        self._emit = _Emitter(events, clock)
        self.last_provider: str | None = None  # who served the most recent successful stream

    def breaker(self, name: str) -> CircuitBreaker:
        return self._breakers[name]

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        span = _tracer.start_span("tts.stream")  # not "current": we yield across it
        span.set_attribute("app.tts.chars", len(text))
        last: ProviderError | None = None
        tried: list[str] = []
        try:
            for idx, provider in enumerate(self.providers):
                name = provider.name
                br = self._breakers[name]
                if not br.allow():
                    tried.append(f"{name}:circuit_open")
                    _skips.add(1, {"kind": "tts", "provider": name})
                    self._emit("provider_skipped_open_circuit", kind="tts", provider=name)
                    continue
                self._emit("provider_attempt", kind="tts", provider=name, attempt=1)
                sent = 0
                t0 = time.perf_counter()
                ttfc_ms: float | None = None
                inner = provider.stream(text)
                it = inner.__aiter__()
                settled = False
                err: ProviderError | None = None
                tag = "error"
                try:
                    while True:
                        limit = self._first_s if sent == 0 else self._chunk_s
                        try:
                            async with asyncio.timeout(limit):
                                chunk = await it.__anext__()
                        except StopAsyncIteration:
                            if sent == 0:
                                raise TTSError(name, "no audio produced", retryable=False) from None
                            break
                        if sent == 0:
                            ttfc_ms = (time.perf_counter() - t0) * 1000
                            self.last_provider = name
                        sent += 1
                        yield chunk
                    br.record(ok=True)
                    settled = True
                    span.set_attribute("app.tts.provider", name)
                    span.set_attribute("app.tts.fallbacks", idx)
                    self._emit(
                        "provider_ok",
                        kind="tts",
                        provider=name,
                        chunks=sent,
                        ttfc_ms=round(ttfc_ms or 0.0, 1),
                        latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                    )
                    return
                except TimeoutError:
                    what = "first chunk" if sent == 0 else f"chunk {sent + 1}"
                    lim = self._first_s if sent == 0 else self._chunk_s
                    err = TTSError(name, f"stalled: no {what} within {lim:g}s", retryable=True)
                    tag = "timeout"
                except ProviderError as exc:
                    err = exc
                except Exception as exc:  # noqa: BLE001
                    err = TTSError(name, f"unexpected {type(exc).__name__}: {exc}", retryable=False)
                    tag = "unexpected_error"
                finally:
                    if not settled and err is None:  # cancelled / closed by the consumer
                        br.record(ok=True)  # barge-in is not a provider failure
                    try:
                        await inner.aclose()  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        log.debug("aclose failed for %s", name, exc_info=True)
                # failure path
                br.record(ok=False)
                last = err
                tried.append(f"{name}:{tag}")
                _errors.add(1, {"kind": "tts", "provider": name, "error": tag})
                self._emit(
                    "provider_error",
                    kind="tts",
                    provider=name,
                    error=str(err),
                    retryable=err.retryable,
                    chunks_already_sent=sent,
                    latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                )
                nxt = self.providers[idx + 1].name if idx + 1 < len(self.providers) else None
                if nxt:
                    _fallbacks.add(1, {"kind": "tts", "from": name, "to": nxt})
                    self._emit(
                        "provider_fallback",
                        kind="tts",
                        provider=name,
                        to=nxt,
                        error=str(err),
                        restart=sent > 0,
                        chunks_already_sent=sent,
                    )
            msg = f"all TTS providers failed: {', '.join(tried) or 'none tried'}"
            exc = TTSError(self.name, msg, retryable=False)
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, msg))
            raise exc from last
        finally:
            span.end()
