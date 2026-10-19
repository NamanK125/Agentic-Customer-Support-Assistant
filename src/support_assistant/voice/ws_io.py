"""Browser audio over a WebSocket: the caller's mic in, the assistant's voice out.

Wire protocol (one socket per call):
- client → server, binary: PCM16 mono 16 kHz caller audio (any length; re-framed here)
- server → client, binary: PCM16 mono 16 kHz assistant audio to play, in order
- server → client, text JSON: {"type": "gain", "value": g} (duck/unduck),
  {"type": "stop"} (barge-in: drop everything queued now), {"type": "event", ...} (call log)

The browser captures with echoCancellation on, so the assistant's own voice coming out of laptop
speakers is mostly removed before it reaches us. That is the in-browser equivalent of the
headphones assumption in mic mode.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from support_assistant.voice.contracts import FRAME_BYTES, pcm_ms

# How far ahead of real time playback may be sent. The browser schedules audio on its own clock,
# so a small lead hides network jitter, but anything sent ahead can't be "unsaid" by stop() on
# the server side; the client drops it on {"type": "stop"}.
_LEAD_S = 0.25


class WebSocketCall:
    """One browser call. Provides an AudioSource (`frames()`) and an AudioSink (`sink`)."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self._frames: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=400)  # ~13 s
        self._send_lock = asyncio.Lock()
        self.closed = asyncio.Event()
        self.sink = WebSocketSink(self)

    async def pump(self) -> None:
        """Read the socket until it closes; re-frame caller audio into FRAME_BYTES frames."""
        buf = bytearray()
        try:
            while True:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data:
                    buf += data
                    while len(buf) >= FRAME_BYTES:
                        frame = bytes(buf[:FRAME_BYTES])
                        del buf[:FRAME_BYTES]
                        if self._frames.full():  # consumer stalled: drop oldest, keep real time
                            self._frames.get_nowait()
                        self._frames.put_nowait(frame)
        except WebSocketDisconnect:
            pass
        finally:
            self.closed.set()
            self._frames.put_nowait(None)

    async def frames(self) -> AsyncIterator[bytes]:
        while True:
            frame = await self._frames.get()
            if frame is None:
                return
            yield frame

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self._send(text=json.dumps(payload, default=str))

    async def send_bytes(self, data: bytes) -> None:
        await self._send(data=data)

    async def _send(self, *, text: str | None = None, data: bytes | None = None) -> None:
        if self.closed.is_set() or self.ws.client_state != WebSocketState.CONNECTED:
            return
        async with self._send_lock:
            try:
                if text is not None:
                    await self.ws.send_text(text)
                else:
                    await self.ws.send_bytes(data or b"")
            except (WebSocketDisconnect, RuntimeError):
                self.closed.set()


class WebSocketSink:
    """AudioSink that plays in the browser. `write` is paced to real time (minus a small lead),
    so the coordinator's idea of "what has been heard" matches the caller's ears."""

    def __init__(self, call: WebSocketCall) -> None:
        self._call = call
        self._play_until = 0.0  # monotonic time at which already-sent audio finishes playing

    async def write(self, pcm: bytes) -> None:
        now = time.monotonic()
        self._play_until = max(self._play_until, now) + pcm_ms(pcm) / 1000
        await self._call.send_bytes(pcm)
        ahead = self._play_until - time.monotonic() - _LEAD_S
        if ahead > 0:
            await asyncio.sleep(ahead)

    def set_gain(self, gain: float) -> None:
        asyncio.get_running_loop().create_task(
            self._call.send_json({"type": "gain", "value": round(gain, 3)})
        )

    async def stop(self) -> None:
        self._play_until = 0.0
        await self._call.send_json({"type": "stop"})
