"""Browser phone line: `/v1/voice` WebSocket calls, fault drills, and the test console page.

Per process (shared by every call): the STT chain (so the local whisper model loads once), the
fault injector (so a drill button affects live calls), the frequency-based TTS cache, and the call
intake. Per call: the TTS chain (always the one configured speaker id, `TTS_SPEAKER`), canned
phrases in that voice, a
fresh VAD, and the coordinator. Each call is logged like a scenario run under demo/runs/web/.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from support_assistant.config import Settings
from support_assistant.contracts import CallerProfile, FinalReply
from support_assistant.voice.contracts import CallEvent
from support_assistant.voice.ws_io import WebSocketCall

log = logging.getLogger(__name__)

_PAGE = Path(__file__).resolve().parents[1] / "web" / "index.html"
_RUNS = Path(__file__).resolve().parents[3] / "demo" / "runs" / "web"

# Shared providers emit through whichever call is currently running them.
_call_sink: contextvars.ContextVar[Any] = contextvars.ContextVar("voice_call_sink", default=None)


def _route_event(ev: CallEvent) -> None:
    sink = _call_sink.get()
    if sink is not None:
        sink(ev)


class FaultRequest(BaseModel):
    target: str | None = None
    mode: str | None = None
    clear: bool = False


class VoiceRuntime:
    """Process-wide voice resources, built once at startup."""

    def __init__(self, settings: Settings, pipeline: Any) -> None:
        from support_assistant.voice.faults import FaultInjector
        from support_assistant.voice.intake import CallIntake
        from support_assistant.voice.providers import build_stt
        from support_assistant.voice.tts_cache import TTSAudioCache

        self.settings = settings
        self.injector = FaultInjector(events=_route_event)
        self.stt = build_stt(settings, self.injector, _route_event)
        self.tts_cache = TTSAudioCache()

        async def handle(session_id: str, text: str, caller: CallerProfile | None) -> FinalReply:
            return await pipeline.handle(session_id, text, "voice", caller)

        self.intake = CallIntake(handle)

    async def warm_up(self) -> None:
        warm = getattr(self.stt, "warm_up", None)
        if warm is not None:
            await warm()


def register_voice(app: FastAPI, settings: Settings) -> None:
    @app.get("/", response_class=HTMLResponse)
    async def page() -> str:
        return _PAGE.read_text()

    def _rt(request_or_ws: Request | WebSocket) -> VoiceRuntime:
        rt = getattr(request_or_ws.app.state, "voice", None)
        if rt is None:
            raise HTTPException(status_code=503, detail="voice not ready")
        return rt

    @app.post("/v1/voice/faults")
    async def faults(body: FaultRequest, request: Request) -> dict[str, Any]:
        rt = _rt(request)
        try:
            if body.clear:
                rt.injector.clear(None)
            elif body.target and body.mode:
                rt.injector.set(body.target, body.mode)
            else:
                raise HTTPException(status_code=422, detail="give target+mode, or clear")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True}

    @app.get("/v1/voice/tts-cache/stats")
    async def tts_cache_stats(request: Request) -> dict[str, Any]:
        return _rt(request).tts_cache.stats()

    @app.websocket("/v1/voice")
    async def voice(ws: WebSocket) -> None:
        await ws.accept()
        rt = getattr(ws.app.state, "voice", None)
        if rt is None:
            await ws.send_text(json.dumps({"type": "error", "message": "voice not ready"}))
            await ws.close()
            return
        await _run_call(ws, rt)


async def _run_call(ws: WebSocket, rt: VoiceRuntime) -> None:
    from support_assistant.voice.canned import CannedAudio
    from support_assistant.voice.coordinator import CallCoordinator
    from support_assistant.voice.intake import GREETING
    from support_assistant.voice.providers import build_tts, call_settings
    from support_assistant.voice.scenario import build_config
    from support_assistant.voice.vad import SileroVAD

    loop = asyncio.get_running_loop()
    epoch = loop.time()
    session = f"voice-web-{uuid.uuid4().hex[:8]}"
    cs = call_settings(rt.settings)  # one fixed speaker id for every call and every request
    run_dir = _RUNS / f"{datetime.now():%Y%m%d-%H%M%S}-{session}"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_fh = (run_dir / "events.jsonl").open("w")
    call = WebSocketCall(ws)

    def clock_ms() -> float:
        return round((loop.time() - epoch) * 1000, 1)

    def on_event(ev: CallEvent) -> None:
        payload = asdict(ev)
        log_fh.write(json.dumps(payload, default=str) + "\n")
        log_fh.flush()
        loop.create_task(
            call.send_json({"type": "event", "session_id": session, "event": payload})
        )

    token = _call_sink.set(on_event)  # tasks spawned below inherit this call's event sink
    pump = loop.create_task(call.pump())
    try:
        tts = build_tts(cs, rt.injector, on_event, clock_ms, cache=rt.tts_cache,
                        session_label=session)  # fmt: skip
        canned = CannedAudio(cs)
        await canned.prepare(tts)
        await tts.prewarm([GREETING])  # pinned: the greeting plays with no synthesis wait
        coord = CallCoordinator(
            call, call.sink, SileroVAD(), rt.stt, tts, rt.intake,
            canned=canned, events=on_event, clock=loop.time, epoch=loop.time(),
            config=build_config({}, session),
        )  # fmt: skip
        await call.send_json(
            {"type": "ready", "session_id": session, "speaker": cs.tts_speaker,
             "log_dir": str(run_dir)}
        )  # fmt: skip
        await coord.run(until=call.closed)
    except Exception as exc:  # a broken call must not take the server down
        log.exception("voice call %s failed", session)
        await call.send_json({"type": "error", "message": repr(exc)})
    finally:
        _call_sink.reset(token)
        rt.intake.end(session)
        pump.cancel()
        log_fh.close()
        if not call.closed.is_set():
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
