"""Factory: the production provider chains, each provider wrapped for fault injection."""

from __future__ import annotations

from collections.abc import Callable

from support_assistant.config import Settings
from support_assistant.voice.contracts import EventSink
from support_assistant.voice.faults import FaultInjector, FaultySTT, FaultyTTS
from support_assistant.voice.resilience import FallbackSTT, FallbackTTS
from support_assistant.voice.stt import GroqWhisperSTT, LocalWhisperSTT
from support_assistant.voice.tts import EdgeTTS, InHouseStreamTTS


def build_stt(
    settings: Settings,
    injector: FaultInjector,
    events: EventSink | None = None,
    clock: Callable[[], float] | None = None,
) -> FallbackSTT:
    return FallbackSTT(
        [
            FaultySTT(GroqWhisperSTT(settings), injector),
            FaultySTT(LocalWhisperSTT(settings), injector),
        ],
        timeout_s=settings.stt_timeout_s,
        timeouts={"local-whisper": settings.stt_local_timeout_s},
        events=events,
        clock=clock,
    )


# Closest Edge TTS voice for each in-house speaker, so a call that fails over keeps a similar
# voice (same gender and accent) instead of jumping to an unrelated one.
EDGE_VOICE_FOR_SPEAKER = {
    "Female, American accent, moderate pitch": "en-US-JennyNeural",
    "Female, British accent, moderate pitch": "en-GB-SoniaNeural",
    "Female, Australian accent, moderate pitch": "en-AU-NatashaNeural",
}


def call_settings(settings: Settings) -> Settings:
    """Settings for one call: every TTS request in the call (greeting, replies, canned phrases,
    replies) uses the one configured speaker id, `TTS_SPEAKER`; nothing per call can
    change it. Failover uses the one fallback voice matching that speaker."""
    fallback = EDGE_VOICE_FOR_SPEAKER.get(settings.tts_speaker, settings.tts_fallback_voice)
    return settings.model_copy(update={"tts_fallback_voice": fallback})


def build_tts(
    settings: Settings,
    injector: FaultInjector,
    events: EventSink | None = None,
    clock: Callable[[], float] | None = None,
    *,
    session_label: str | None = None,
) -> FallbackTTS:
    """TTS chain for one call, always in the one configured voice (`TTS_SPEAKER`).
    `session_label` tags every TTS request of the call."""
    s = call_settings(settings)
    inhouse = InHouseStreamTTS(s, session_label=session_label)
    return FallbackTTS(
        [FaultyTTS(inhouse, injector), FaultyTTS(EdgeTTS(s), injector)],
        first_chunk_timeout_s=s.tts_first_chunk_timeout_s,
        chunk_timeout_s=s.tts_chunk_timeout_s,
        events=events,
        clock=clock,
    )
