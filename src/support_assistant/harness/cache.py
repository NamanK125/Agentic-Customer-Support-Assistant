"""Classifier cache: memoises the router LLM's validated output.

Key = sha256(prompt version, model id, normalised last assistant turn, normalised user text with
order ids masked). The key covers exactly what the classifier sees, so a context-dependent
message ("yes", "that one") only hits when the preceding assistant turn is the same too. Order ids
are masked because they do not change the intent and the router re-extracts them by regex on every
turn; without masking every order question would be a unique key.

What is cached is the raw validated classifier output, not the final RouteDecision, so threshold
changes apply to cached entries and a cached order id is re-checked against the live message.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, computed_field
from redis.asyncio import Redis
from redis.exceptions import RedisError

from support_assistant.observability import meter
from support_assistant.tools.orders import ORDER_ID_RE

_meter = meter(__name__)
_hits = _meter.create_counter("router.cache.hits", description="classifier cache hits")
_misses = _meter.create_counter("router.cache.misses", description="classifier cache misses")
_PUNCT = re.compile(r"[^\w#+]+")  # keep '+' (Care+) and the '#' of masked ids


def normalize(text: str) -> str:
    """Case, whitespace and punctuation-insensitive; order ids masked."""
    return _PUNCT.sub(" ", ORDER_ID_RE.sub("PN-#####", text)).strip().lower()


def make_cache_key(text: str, *, context: str, model_id: str, prompt_version: str) -> str:
    parts = (prompt_version, model_id, normalize(context), normalize(text))
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


class CacheStats(BaseModel):
    backend: str
    hits: int
    misses: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


@runtime_checkable
class ClassifierCache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str) -> None: ...
    async def stats(self) -> CacheStats: ...


class InMemoryTTLCache:
    """LRU + TTL, per process. Methods never await between read and write, so they are atomic
    on the event loop and need no lock."""

    backend = "memory"

    def __init__(
        self,
        *,
        max_entries: int = 10_000,
        ttl_s: float = 3600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._data: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._max = max_entries
        self._ttl = ttl_s
        self._clock = clock
        self._hits = 0
        self._misses = 0

    async def get(self, key: str) -> str | None:
        entry = self._data.get(key)
        if entry is not None and entry[0] > self._clock():
            self._data.move_to_end(key)
            self._hits += 1
            _hits.add(1, {"backend": self.backend})
            return entry[1]
        if entry is not None:
            del self._data[key]
        self._misses += 1
        _misses.add(1, {"backend": self.backend})
        return None

    async def set(self, key: str, value: str) -> None:
        self._data[key] = (self._clock() + self._ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    async def stats(self) -> CacheStats:
        return CacheStats(backend=self.backend, hits=self._hits, misses=self._misses)


class RedisClassifierCache:
    """Shared across replicas. Redis errors degrade to a miss (the cache is an optimisation).
    Hit/miss counters live in Redis too, so `stats()` reports the fleet-wide hit rate."""

    backend = "redis"

    def __init__(self, redis: Redis, *, ttl_s: int = 3600, prefix: str = "sa:router:") -> None:
        self._r = redis
        self._ttl = ttl_s
        self._prefix = prefix

    async def _count(self, hit: bool) -> None:
        (_hits if hit else _misses).add(1, {"backend": self.backend})
        try:
            await self._r.incr(f"{self._prefix}stats:{'hits' if hit else 'misses'}")
        except RedisError:
            pass

    async def get(self, key: str) -> str | None:
        try:
            raw = await self._r.get(self._prefix + key)
        except RedisError:
            raw = None
        await self._count(raw is not None)
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    async def set(self, key: str, value: str) -> None:
        try:
            await self._r.set(self._prefix + key, value, ex=self._ttl)
        except RedisError:
            pass

    async def stats(self) -> CacheStats:
        try:
            hits, misses = await self._r.mget(
                f"{self._prefix}stats:hits", f"{self._prefix}stats:misses"
            )
        except RedisError:
            hits = misses = None
        return CacheStats(backend=self.backend, hits=int(hits or 0), misses=int(misses or 0))
