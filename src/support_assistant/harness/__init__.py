"""Orchestration harness: handler registry, router, classifier cache, sessions, pipeline.

`build_pipeline` lives in `support_assistant.harness.factory` and is not re-exported here, so
importing the harness never pulls in the guardrails/RAG packages or their heavy dependencies.
"""

from support_assistant.harness.cache import (
    CacheStats,
    ClassifierCache,
    InMemoryTTLCache,
    RedisClassifierCache,
    make_cache_key,
)
from support_assistant.harness.pipeline import SAFE_FALLBACK, SupportPipeline
from support_assistant.harness.registry import DuplicateIntentError, HandlerRegistry
from support_assistant.harness.router import LLMRouter
from support_assistant.harness.session import (
    InMemorySessionStore,
    RedisSessionStore,
    SessionStore,
)

__all__ = [
    "SAFE_FALLBACK",
    "CacheStats",
    "ClassifierCache",
    "DuplicateIntentError",
    "HandlerRegistry",
    "InMemorySessionStore",
    "InMemoryTTLCache",
    "LLMRouter",
    "RedisClassifierCache",
    "RedisSessionStore",
    "SessionStore",
    "SupportPipeline",
    "make_cache_key",
]
