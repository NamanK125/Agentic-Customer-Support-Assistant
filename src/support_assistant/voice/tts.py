"""TTS providers: the in-house streaming WebSocket service and Edge TTS (MP3 -> PCM16/16 kHz).

Both map every failure to TTSError and stop remote synthesis the moment the consumer closes the
iterator (barge-in): the socket is aborted rather than closed politely.
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from support_assistant.config import Settings, get_settings
from support_assistant.voice.contracts import SAMPLE_RATE, TTSError

log = logging.getLogger(__name__)


class InHouseStreamTTS:
    """One WebSocket per `stream()` call; yields each binary PCM frame as it arrives."""

    name = "inhouse"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        url: str | None = None,
        open_timeout_s: float = 5,
        session_label: str | None = None,
    ) -> None:
        self._s = settings or get_settings()
        self._url = url or self._s.tts_ws_url
        self._open_timeout_s = open_timeout_s
        # Every request in one call is labelled "<call id>-r<n>" so the TTS service's logs and
        # metadata show which requests belong to the same call (and that they share one speaker).
        self._label = session_label
        self._requests = 0

    def with_url(self, url: str) -> InHouseStreamTTS:
        return InHouseStreamTTS(
            self._s, url=url, open_timeout_s=self._open_timeout_s, session_label=self._label
        )

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        try:
            ws = await websockets.connect(
                self._url, open_timeout=self._open_timeout_s, max_size=None, ping_interval=None
            )
        except InvalidStatus as exc:
            code = exc.response.status_code
            raise TTSError(
                self.name, f"handshake rejected: HTTP {code}", retryable=code >= 500
            ) from exc
        except (OSError, TimeoutError, InvalidHandshake) as exc:
            raise TTSError(
                self.name, f"cannot connect to {self._url}: {exc!r}", retryable=True
            ) from exc
        done = False
        try:
            await self._send_request(ws, text)
            carry = b""
            while True:
                msg = await ws.recv()
                if isinstance(msg, bytes):
                    data = carry + strip_wav_header(msg, self.name)
                    cut = len(data) - (len(data) % 2)  # keep PCM16 sample alignment
                    carry = data[cut:]
                    if cut:
                        yield data[:cut]
                    continue
                try:
                    ev = json.loads(msg)
                except json.JSONDecodeError as exc:
                    raise TTSError(
                        self.name, f"non-JSON text frame: {msg[:80]!r}", retryable=False
                    ) from exc
                kind = ev.get("type") if isinstance(ev, dict) else None
                if kind == "done":
                    done = True
                    await ws.send(json.dumps({"type": "client_complete"}))
                    return
                if kind == "error":
                    raise TTSError(self.name, f"server error: {ev.get('msg')}", retryable=False)
                # "metadata" / "chunk" headers carry nothing we need; the audio is the next frame
        except ConnectionClosed as exc:
            raise TTSError(
                self.name, f"connection closed before done: {exc}", retryable=True
            ) from exc
        except OSError as exc:
            raise TTSError(self.name, f"network error: {exc!r}", retryable=True) from exc
        finally:
            await _close(ws, graceful=done)

    async def _send_request(self, ws: ClientConnection, text: str) -> None:
        cfg = {
            "type": "config",
            "languages": [self._s.tts_language],
            "audio_codec": "pcm_16000",
            "speaker_id": self._s.tts_speaker,
            "eager": True,
            "skip_normalize_input": not self._s.tts_normalize,
            "transliterate_capitals": self._s.tts_phonemize,
        }
        if self._label:
            self._requests += 1
            cfg["tts_session_id"] = f"{self._label}-r{self._requests}"
        await ws.send(json.dumps(cfg))
        await ws.send(json.dumps({"type": "text", "text_input": text}))
        await ws.send(json.dumps({"type": "flush"}))


def strip_wav_header(frame: bytes, provider: str) -> bytes:
    """Return the PCM payload of one audio frame.

    Even with `audio_codec="pcm_16000"` the service sends each chunk as a complete WAV file: a
    44-byte RIFF header, then the samples. Played as audio, those header bytes are 22 samples of
    up to ±18,000, which is an audible click at the start of every chunk. We parse the RIFF chunk
    list rather than dropping a fixed 44 bytes, so an extra chunk (e.g. LIST) is handled too, and
    we refuse audio that isn't PCM16 mono 16 kHz instead of playing it at the wrong speed.
    """
    if not frame.startswith(b"RIFF") or frame[8:12] != b"WAVE":
        return frame  # already raw PCM
    pos = 12
    while pos + 8 <= len(frame):
        cid, size = frame[pos : pos + 4], struct.unpack_from("<I", frame, pos + 4)[0]
        body = pos + 8
        if cid == b"fmt ":
            fmt_tag, channels, rate = struct.unpack_from("<HHI", frame, body)
            bits = struct.unpack_from("<H", frame, body + 14)[0]
            if (fmt_tag, channels, rate, bits) != (1, 1, SAMPLE_RATE, 16):
                raise TTSError(
                    provider,
                    f"unexpected audio format tag={fmt_tag} ch={channels} rate={rate} bits={bits}",
                    retryable=False,
                )
        elif cid == b"data":
            return frame[body : body + size]
        pos = body + size + (size & 1)  # chunks are word-aligned
    raise TTSError(provider, "WAV frame without a data chunk", retryable=False)


async def _close(ws: ClientConnection, *, graceful: bool) -> None:
    """Graceful on normal completion; on early exit (barge-in) drop the TCP connection now."""
    try:
        if graceful:
            await asyncio.wait_for(ws.close(), 1.0)
            return
    except Exception:  # noqa: BLE001
        pass
    transport = getattr(ws, "transport", None)
    if transport is not None:
        transport.abort()
    try:
        await asyncio.wait_for(ws.wait_closed(), 0.5)
    except Exception:  # noqa: BLE001
        pass


class _Mp3ToPcm:
    """Incremental MP3 -> PCM16 mono 16 kHz. Blocking (PyAV); call from a thread."""

    def __init__(self) -> None:
        import av

        self._av = av
        self._codec = av.CodecContext.create("mp3", "r")
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)

    def _frames_to_pcm(self, frames: list[Any]) -> bytes:
        out = bytearray()
        for frame in frames:
            for rs in self._resampler.resample(frame):
                out += bytes(rs.planes[0])[: rs.samples * 2]
        return bytes(out)

    def feed(self, data: bytes) -> bytes:
        frames: list[Any] = []
        for packet in self._codec.parse(data):
            frames.extend(self._codec.decode(packet))
        return self._frames_to_pcm(frames)

    def flush(self) -> bytes:
        out = b""
        try:  # the tail is a few ms; a decoder quirk here must not fail an otherwise good stream
            out = self._frames_to_pcm(list(self._codec.decode(None)))
            out += self._frames_to_pcm(list(self._resampler.resample(None)))
        except (ValueError, self._av.error.FFmpegError):
            log.debug("mp3 flush skipped", exc_info=True)
        return out


class EdgeTTS:
    """Microsoft Edge neural voices over the public endpoint (internet, no key)."""

    name = "edge"

    def __init__(self, settings: Settings | None = None, *, voice: str | None = None) -> None:
        self._s = settings or get_settings()
        self._voice = voice or self._s.tts_fallback_voice

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        import edge_tts

        decoder = await asyncio.to_thread(_Mp3ToPcm)
        src = edge_tts.Communicate(text, self._voice).stream()
        try:
            async for item in src:
                if item.get("type") != "audio":
                    continue
                pcm = await asyncio.to_thread(decoder.feed, item["data"])
                if pcm:
                    yield pcm
            tail = await asyncio.to_thread(decoder.flush)
            if tail:
                yield tail
        except TTSError:
            raise
        except Exception as exc:  # noqa: BLE001 - aiohttp / edge_tts / PyAV errors alike
            raise TTSError(self.name, f"{type(exc).__name__}: {exc}", retryable=True) from exc
        finally:
            await src.aclose()
