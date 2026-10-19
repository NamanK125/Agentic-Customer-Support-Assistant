"""Voice-side types and interfaces. Providers, the coordinator and the runner code against these.

Audio everywhere inside the voice package is mono PCM16 little-endian at 16 kHz, moved in frames
of 512 samples (32 ms), which is the frame size Silero VAD expects at 16 kHz. Providers convert
at their edge (e.g. a TTS that returns MP3 decodes it before yielding).

One call:

    AudioSource --frames--> VAD/endpointer --utterance pcm--> STT --text--> Responder (the text
    SupportPipeline, channel="voice") --FinalReply.text--> TTS --pcm chunks--> AudioSink

While the sink is playing, the coordinator keeps reading the source; real user speech (not noise)
ducks and then cancels playback (barge-in) and becomes the next utterance.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2  # bytes, PCM16
FRAME_SAMPLES = 512
FRAME_MS = FRAME_SAMPLES * 1000 / SAMPLE_RATE  # 32.0
FRAME_BYTES = FRAME_SAMPLES * SAMPLE_WIDTH


def pcm_ms(pcm: bytes) -> float:
    """Duration of a PCM16 mono 16 kHz buffer in milliseconds."""
    return len(pcm) / SAMPLE_WIDTH * 1000 / SAMPLE_RATE


# ---- Errors: providers raise these (never bare library exceptions) so chains can fall back ----


class ProviderError(RuntimeError):
    """A provider failed. `retryable` = worth retrying the same provider (timeouts, 5xx, network);
    False for auth/config errors where only a different provider can help."""

    def __init__(self, provider: str, message: str, *, retryable: bool) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable


class STTError(ProviderError):
    pass


class TTSError(ProviderError):
    pass


# ---- STT -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Transcript:
    text: str
    provider: str
    latency_ms: float
    language: str | None = None
    attempts: tuple[str, ...] = ()  # e.g. ("groq:timeout", "local-whisper:ok") for the call log


@runtime_checkable
class STT(Protocol):
    name: str

    async def transcribe(self, pcm: bytes) -> Transcript:
        """Transcribe one complete utterance. Raises STTError; must honour cancellation."""
        ...


# ---- TTS -----------------------------------------------------------------------------------


@runtime_checkable
class TTS(Protocol):
    name: str

    def stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield PCM16/16 kHz chunks as soon as they are available.

        Raises TTSError (before the first chunk or mid-stream). Cancelling the consuming task, or
        calling `aclose()` on the iterator, must stop the remote synthesis promptly; barge-in
        depends on it.
        """
        ...


# ---- Audio I/O -----------------------------------------------------------------------------


@runtime_checkable
class AudioSource(Protocol):
    """Live mic or scripted clip injection. Yields FRAME_BYTES frames paced in real time."""

    def frames(self) -> AsyncIterator[bytes]: ...


@runtime_checkable
class AudioSink(Protocol):
    """Speaker or recorder. `write` returns once the audio has been handed to the device (paced
    in real time), so the time of the first `write` is the time the caller starts hearing it."""

    async def write(self, pcm: bytes) -> None: ...

    def set_gain(self, gain: float) -> None:
        """0..1. Used to duck playback while a possible barge-in is being confirmed."""
        ...

    async def stop(self) -> None:
        """Drop anything queued and stop sound immediately (barge-in)."""
        ...


# ---- VAD -----------------------------------------------------------------------------------


@runtime_checkable
class VAD(Protocol):
    def speech_prob(self, frame: bytes) -> float:
        """Probability (0..1) that this FRAME_BYTES frame contains speech. Fast and synchronous;
        stateful implementations are reset with `reset()`."""
        ...

    def reset(self) -> None: ...


# ---- Responder: the text side ----------------------------------------------------------------


@dataclass(frozen=True)
class VoiceReply:
    text: str
    blocked: bool = False
    rewritten: bool = False
    intent: str | None = None
    trace_id: str | None = None


Responder = Callable[[str, str], Awaitable[VoiceReply]]  # (session_id, user_text) -> reply


# ---- Call events (the demo logs are made of these) --------------------------------------------


@dataclass
class CallEvent:
    t_ms: float  # ms since call start (monotonic)
    kind: str  # e.g. "speech_start", "eou", "stt_done", "barge_in_confirmed", "provider_fallback"
    turn: int | None = None
    data: dict[str, Any] = field(default_factory=dict)


EventSink = Callable[[CallEvent], None]
