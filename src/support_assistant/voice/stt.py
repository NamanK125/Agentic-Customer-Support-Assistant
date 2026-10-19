"""STT providers: Groq Whisper (OpenAI-compatible HTTP) and local faster-whisper (offline)."""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
import wave
from typing import Any

import numpy as np
import openai
from openai import AsyncOpenAI

from support_assistant.config import Settings, get_settings
from support_assistant.voice.contracts import SAMPLE_RATE, SAMPLE_WIDTH, STTError, Transcript

log = logging.getLogger(__name__)

_NO_KEY = {"", "EMPTY", "empty", "none", "None"}


def pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


class GroqWhisperSTT:
    name = "groq-whisper"

    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self._key = self._s.stt_api_key or self._s.llm_api_key
        self._client: AsyncOpenAI | None = None

    @property
    def has_key(self) -> bool:
        return self._key.strip() not in _NO_KEY

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=self._s.stt_base_url,
                api_key=self._key,
                timeout=self._s.stt_timeout_s,
                max_retries=0,  # retries belong to FallbackSTT, so they show up in events
            )
        return self._client

    async def transcribe(self, pcm: bytes) -> Transcript:
        if not self.has_key:
            raise STTError(
                self.name, "no API key configured (STT_API_KEY / LLM_API_KEY)", retryable=False
            )
        t0 = time.perf_counter()
        try:
            resp = await self._get_client().audio.transcriptions.create(
                model=self._s.stt_model,
                file=("utterance.wav", pcm_to_wav(pcm)),
                language=self._s.stt_language or openai.NOT_GIVEN,
                prompt=self._s.stt_prompt or openai.NOT_GIVEN,
                temperature=0,
                response_format="json",
            )
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise STTError(self.name, f"auth failed: {exc}", retryable=False) from exc
        except (
            openai.RateLimitError,
            openai.InternalServerError,
            openai.APITimeoutError,
            openai.APIConnectionError,
        ) as exc:
            raise STTError(self.name, f"{type(exc).__name__}: {exc}", retryable=True) from exc
        except openai.APIError as exc:
            raise STTError(self.name, f"{type(exc).__name__}: {exc}", retryable=False) from exc
        return Transcript(
            text=(resp.text or "").strip(),
            provider=self.name,
            latency_ms=(time.perf_counter() - t0) * 1000,
            language=self._s.stt_language,
        )


class LocalWhisperSTT:
    """Offline fallback: faster-whisper on CPU, int8. The first load may download the model, so
    the coordinator should `await warm_up()` at startup."""

    name = "local-whisper"

    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                t0 = time.perf_counter()
                self._model = WhisperModel(
                    self._s.stt_local_model, device="cpu", compute_type="int8"
                )
                log.info(
                    "local whisper '%s' loaded in %.1fs",
                    self._s.stt_local_model,
                    time.perf_counter() - t0,
                )
            return self._model

    async def warm_up(self) -> None:
        await asyncio.to_thread(self._load)

    def _run(self, pcm: bytes) -> str:
        model = self._load()
        audio = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = model.transcribe(
            audio,
            beam_size=1,
            initial_prompt=self._s.stt_prompt or None,
            vad_filter=False,
            language=self._s.stt_language,
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    async def transcribe(self, pcm: bytes) -> Transcript:
        t0 = time.perf_counter()
        try:
            text = await asyncio.to_thread(self._run, pcm)
        except Exception as exc:  # noqa: BLE001 - model download / decode / runtime errors
            raise STTError(self.name, f"{type(exc).__name__}: {exc}", retryable=False) from exc
        return Transcript(
            text=text,
            provider=self.name,
            latency_ms=(time.perf_counter() - t0) * 1000,
            language=self._s.stt_language,
        )
