"""Composition root: the one place that knows concrete classes. Everything else codes to ports."""

from __future__ import annotations

from pathlib import Path

from support_assistant.config import Settings, get_settings
from support_assistant.harness.pipeline import SupportPipeline


def build_pipeline(settings: Settings | None = None) -> SupportPipeline:
    """Wire the real components. Sync and potentially slow (embedding model load), so async
    callers should run it in a thread. Stores are Redis-backed when REDIS_URL is set."""
    s = settings or get_settings()

    # Imported lazily: other packages, and heavy deps (sentence-transformers) load only here.
    from support_assistant.agents import (
        EscalationHandler,
        InMemoryHandoffStore,
        RedisHandoffStore,
        ToolAgent,
    )
    from support_assistant.guardrails import InputGuardrail, OutputGuardrail
    from support_assistant.harness.cache import (
        ClassifierCache,
        InMemoryTTLCache,
        RedisClassifierCache,
    )
    from support_assistant.harness.registry import HandlerRegistry
    from support_assistant.harness.router import LLMRouter
    from support_assistant.harness.session import (
        InMemorySessionStore,
        RedisSessionStore,
        SessionStore,
    )
    from support_assistant.llm import LLMClient
    from support_assistant.rag import RagAgent, build_retriever
    from support_assistant.tools import JsonOrderStore, OrderTools
    from support_assistant.tools.orders import DEFAULT_ORDERS_PATH

    llm = LLMClient(s)

    cache: ClassifierCache
    sessions: SessionStore
    if s.redis_url:
        from redis.asyncio import Redis

        redis = Redis.from_url(s.redis_url, max_connections=200, health_check_interval=30)
        cache = RedisClassifierCache(redis, ttl_s=s.cache_ttl_s)
        sessions = RedisSessionStore(redis)
        handoffs = RedisHandoffStore(redis)
    else:
        cache = InMemoryTTLCache(ttl_s=s.cache_ttl_s)
        sessions = InMemorySessionStore()
        handoffs = InMemoryHandoffStore()

    orders = JsonOrderStore(Path(s.orders_path) if s.orders_path else DEFAULT_ORDERS_PATH)
    registry = HandlerRegistry([
        RagAgent(llm, build_retriever(s)),
        ToolAgent(llm, OrderTools(orders)),
        EscalationHandler(handoffs),
    ])  # fmt: skip
    router = LLMRouter(
        llm,
        registry,
        cache=cache,
        threshold=s.router_confidence_threshold,
        model_id=s.model_for("router"),
        timeout_s=min(s.llm_timeout_s, 8.0),
    )
    return SupportPipeline(
        # Measured 2.5-3.4 s for the input classifier on the in-house server (~10 tok/s decode);
        # the 4 s default would time out often and silently drop to heuristic-only mode.
        InputGuardrail(llm, llm_timeout_s=6.0),
        router,
        registry,
        OutputGuardrail(llm),
        sessions,
        turn_timeout_s=s.turn_timeout_s,
    )
