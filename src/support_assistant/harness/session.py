"""Conversation history per session (last N turns), used to fill RawMessage.history.

Workers stay stateless: with Redis, any replica can serve any turn of any session.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from typing import Protocol, runtime_checkable

from redis.asyncio import Redis

from support_assistant.contracts import ChatTurn


@runtime_checkable
class SessionStore(Protocol):
    async def history(self, session_id: str) -> tuple[ChatTurn, ...]: ...
    async def append(self, session_id: str, *turns: ChatTurn) -> None: ...


class InMemorySessionStore:
    """Single-process store; LRU-bounded on sessions so it cannot grow without limit."""

    def __init__(self, *, max_turns: int = 10, max_sessions: int = 10_000) -> None:
        self._max_turns = max_turns
        self._max_sessions = max_sessions
        self._data: OrderedDict[str, deque[ChatTurn]] = OrderedDict()

    async def history(self, session_id: str) -> tuple[ChatTurn, ...]:
        turns = self._data.get(session_id)
        return tuple(turns) if turns else ()

    async def append(self, session_id: str, *turns: ChatTurn) -> None:
        buf = self._data.setdefault(session_id, deque(maxlen=self._max_turns))
        buf.extend(turns)
        self._data.move_to_end(session_id)
        while len(self._data) > self._max_sessions:
            self._data.popitem(last=False)


class RedisSessionStore:
    def __init__(
        self, redis: Redis, *, max_turns: int = 10, ttl_s: int = 86_400, prefix: str = "sa:session:"
    ) -> None:
        self._r = redis
        self._max_turns = max_turns
        self._ttl = ttl_s
        self._prefix = prefix

    async def history(self, session_id: str) -> tuple[ChatTurn, ...]:
        raw = await self._r.lrange(self._prefix + session_id, 0, -1)
        return tuple(ChatTurn.model_validate_json(item) for item in raw)

    async def append(self, session_id: str, *turns: ChatTurn) -> None:
        if not turns:
            return
        key = self._prefix + session_id
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.rpush(key, *(t.model_dump_json() for t in turns))
            pipe.ltrim(key, -self._max_turns, -1)
            pipe.expire(key, self._ttl)
            await pipe.execute()
