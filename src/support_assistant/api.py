"""HTTP API. Stateless: all conversation state goes through the pipeline's stores, so any replica
can serve any turn. Run with `uvicorn support_assistant.api:create_app --factory`."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal, Protocol

from fastapi import FastAPI, HTTPException, Request
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel, Field

from support_assistant.config import Settings, get_settings
from support_assistant.contracts import CallerProfile, FinalReply
from support_assistant.harness.cache import CacheStats
from support_assistant.observability import setup_telemetry


class ChatPipeline(Protocol):
    async def handle(
        self,
        session_id: str,
        text: str,
        channel: Literal["text", "voice"] = "text",
        caller: CallerProfile | None = None,
    ) -> FinalReply: ...
    async def cache_stats(self) -> CacheStats | None: ...


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    text: str = Field(min_length=1, max_length=4000)
    channel: Literal["text", "voice"] = "text"


def create_app(
    pipeline: ChatPipeline | None = None,
    settings: Settings | None = None,
    *,
    voice: bool | None = None,
) -> FastAPI:
    s = settings or get_settings()
    if voice is None:  # the real app gets voice; tests that inject a fake pipeline don't
        voice = pipeline is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_telemetry(s)
        if pipeline is None:
            from support_assistant.harness.factory import build_pipeline

            # Model loading is blocking; keep it off the event loop.
            app.state.pipeline = await asyncio.to_thread(build_pipeline, s)
        else:
            app.state.pipeline = pipeline
        if voice:
            from support_assistant.voice.web import VoiceRuntime

            # Builds the shared STT chain and loads the local whisper model once, off the loop.
            app.state.voice = VoiceRuntime(s, app.state.pipeline)
            await app.state.voice.warm_up()
        yield

    app = FastAPI(title="PulseNest support assistant", version="0.1.0", lifespan=lifespan)

    def _pipeline(request: Request) -> ChatPipeline:
        p = getattr(request.app.state, "pipeline", None)
        if p is None:
            raise HTTPException(status_code=503, detail="pipeline not ready")
        return p

    @app.post("/v1/chat", response_model=FinalReply)
    async def chat(body: ChatRequest, request: Request) -> FinalReply:
        return await _pipeline(request).handle(body.session_id, body.text, body.channel)

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, str]:
        ready = getattr(request.app.state, "pipeline", None) is not None
        return {"status": "ok" if ready else "starting"}

    @app.get("/v1/cache/stats")
    async def cache_stats(request: Request) -> CacheStats:
        stats = await _pipeline(request).cache_stats()
        return stats or CacheStats(backend="none", hits=0, misses=0)

    if voice:
        from support_assistant.voice.web import register_voice

        register_voice(app, s)  # test console at "/", /v1/voice WebSocket, fault drills

    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz")
    return app
