"""Classifier caches, session stores and handoff stores: in-memory and Redis (fakeredis)."""

from __future__ import annotations

import fakeredis.aioredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from support_assistant.agents import HandoffTicket, InMemoryHandoffStore, RedisHandoffStore
from support_assistant.contracts import ChatTurn
from support_assistant.harness import (
    InMemorySessionStore,
    InMemoryTTLCache,
    RedisClassifierCache,
    RedisSessionStore,
    make_cache_key,
)

from .helpers import FIXED_NOW


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis()


def test_cache_key_normalises_and_masks_order_ids():
    k = lambda t, c="": make_cache_key(t, context=c, model_id="m", prompt_version="v1")  # noqa: E731
    assert k("Where is PN-10001?") == k("  where is   pn 10002?")
    assert k("yes", "Start a return?") != k("yes", "Talk to a human?")
    assert make_cache_key("a", context="", model_id="m1", prompt_version="v1") != make_cache_key(
        "a", context="", model_id="m2", prompt_version="v1"
    )


async def test_memory_cache_ttl_and_lru():
    now = [0.0]
    cache = InMemoryTTLCache(max_entries=2, ttl_s=10, clock=lambda: now[0])
    await cache.set("a", "1")
    await cache.set("b", "2")
    assert await cache.get("a") == "1"  # a is now most recent
    await cache.set("c", "3")  # evicts b
    assert await cache.get("b") is None
    now[0] = 11
    assert await cache.get("a") is None  # expired
    s = await cache.stats()
    assert (s.hits, s.misses) == (1, 2) and s.hit_rate == pytest.approx(1 / 3)


async def test_redis_cache_roundtrip_and_fleet_stats(redis):
    a, b = RedisClassifierCache(redis, ttl_s=60), RedisClassifierCache(redis, ttl_s=60)
    assert await a.get("k") is None
    await a.set("k", '{"x":1}')
    assert await b.get("k") == '{"x":1}'  # shared across replicas
    assert await redis.ttl("sa:router:k") > 0
    s = await a.stats()
    assert (s.hits, s.misses, s.hit_rate) == (1, 1, 0.5)


async def test_redis_cache_outage_is_a_miss():
    class Down:
        async def get(self, *_a, **_k):
            raise RedisConnectionError("down")

        set = incr = mget = get

    cache = RedisClassifierCache(Down())  # type: ignore[arg-type]
    assert await cache.get("k") is None
    await cache.set("k", "v")  # no raise
    assert (await cache.stats()).hits == 0


@pytest.mark.parametrize("kind", ["memory", "redis"])
async def test_session_store_keeps_last_n(kind, redis):
    store = (
        InMemorySessionStore(max_turns=3)
        if kind == "memory"
        else RedisSessionStore(redis, max_turns=3)
    )
    assert await store.history("s") == ()
    for i in range(5):
        await store.append("s", ChatTurn(role="user", text=str(i)))
    assert [t.text for t in await store.history("s")] == ["2", "3", "4"]
    assert await store.history("other") == ()


async def test_memory_session_store_bounds_sessions():
    store = InMemorySessionStore(max_sessions=2)
    for sid in "abc":
        await store.append(sid, ChatTurn(role="user", text=sid))
    assert await store.history("a") == ()
    assert len(await store.history("c")) == 1


@pytest.mark.parametrize("kind", ["memory", "redis"])
async def test_handoff_store(kind, redis):
    store = InMemoryHandoffStore() if kind == "memory" else RedisHandoffStore(redis)
    t = HandoffTicket(
        ticket_id="HT-1", session_id="s", created_at=FIXED_NOW, customer_message="help",
        route_rationale="asked for human",
    )  # fmt: skip
    await store.create(t)
    assert await store.get("HT-1") == t
    assert await store.get("HT-2") is None
    if kind == "redis":
        assert await redis.lrange("sa:handoff:queue", 0, -1) == [b"HT-1"]
