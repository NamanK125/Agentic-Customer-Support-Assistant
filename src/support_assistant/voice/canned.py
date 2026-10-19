"""Pre-rendered stock phrases, so the caller hears something even when the pipeline is struggling.

`prepare(tts)` renders each phrase once (cached on disk by text + voice). If synthesis is
impossible at startup, a short two-tone chime stands in, so feedback is still audible.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

import numpy as np

from support_assistant.config import Settings, get_settings
from support_assistant.voice.contracts import SAMPLE_RATE, TTS

log = logging.getLogger(__name__)

PHRASES: dict[str, str] = {
    "didnt_catch": "Sorry, I didn't catch that. Could you say it again?",
    "one_moment": "One moment, let me check that.",
    "trouble": (
        "Sorry, I'm having trouble on my side. Please hold on, or I can connect you with a person."
    ),
}
_DEFAULT_DIR = Path(__file__).resolve().parents[3] / ".cache" / "voice" / "canned"


def make_chime() -> bytes:
    """Two short tones (880 Hz then 660 Hz) as PCM16/16 kHz, with fades to avoid clicks."""
    parts = []
    for freq in (880.0, 660.0):
        t = np.arange(int(SAMPLE_RATE * 0.18)) / SAMPLE_RATE
        wave = np.sin(2 * np.pi * freq * t) * np.minimum(1.0, np.minimum(t, t[-1] - t) / 0.02)
        parts.append((wave * 0.3 * 32767).astype(np.int16))
    return np.concatenate(parts).tobytes()


class CannedAudio:
    def __init__(
        self,
        settings: Settings | None = None,
        cache_dir: Path | None = None,
        *,
        synth_timeout_s: float = 20.0,
    ) -> None:
        self._s = settings or get_settings()
        self._dir = cache_dir or _DEFAULT_DIR
        self._timeout_s = synth_timeout_s
        self._audio: dict[str, bytes] = {}
        self._chime = make_chime()
        self.rendered_with: dict[
            str, str
        ] = {}  # phrase -> "cache:<voice>" | "<provider>" | "chime"

    def _voices(self) -> list[tuple[str, str]]:
        # preference order: primary voice, then the fallback voice
        primary = (
            f"{self._s.tts_speaker}|norm={int(self._s.tts_normalize)}"
            f"|phon={int(self._s.tts_phonemize)}"
        )  # same identity as providers.voice_key
        return [("inhouse", primary), ("edge", self._s.tts_fallback_voice)]

    def _path(self, text: str, voice: str) -> Path:
        return self._dir / f"{hashlib.sha1(f'{text}|{voice}'.encode()).hexdigest()[:16]}.pcm"

    async def prepare(self, tts: TTS) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        primary_name, primary_voice = self._voices()[0]
        for key, text in PHRASES.items():
            primary = self._path(text, primary_voice)
            if primary.exists():
                self._audio[key] = primary.read_bytes()
                self.rendered_with[key] = f"cache:{primary_name}"
                continue
            pcm = await self._synthesize(tts, text)
            if pcm:
                served = getattr(tts, "last_provider", None) or tts.name
                voice = dict((n, v) for n, v in self._voices()).get(served, served)
                self._path(text, voice).write_bytes(pcm)
                self._audio[key] = pcm
                self.rendered_with[key] = served
                continue
            for name, voice in self._voices()[1:]:  # TTS down: any earlier fallback-voice render
                p = self._path(text, voice)
                if p.exists():
                    self._audio[key] = p.read_bytes()
                    self.rendered_with[key] = f"cache:{name}"
                    break
            else:
                log.warning("canned phrase %r: no TTS and no cache; using chime", key)
                self._audio[key] = self._chime
                self.rendered_with[key] = "chime"

    async def _synthesize(self, tts: TTS, text: str) -> bytes:
        async def run() -> bytes:
            return b"".join([c async for c in tts.stream(text)])

        try:
            return await asyncio.wait_for(run(), self._timeout_s)
        except Exception as exc:  # noqa: BLE001
            log.warning("canned synthesis failed: %s", exc)
            return b""

    def get(self, name: str) -> bytes:
        """PCM for a phrase. Never raises: unknown or unprepared names get the chime."""
        return self._audio.get(name) or self._chime

    def text(self, name: str) -> str:
        return PHRASES[name]
