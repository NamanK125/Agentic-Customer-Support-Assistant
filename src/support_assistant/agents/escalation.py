"""Escalation handler: files a handoff ticket for a human agent. Deterministic, no LLM, so it is
the safe destination for low-confidence routes, router failures and degraded mode."""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis

from support_assistant.contracts import CheckedMessage, DraftReply, Handler, Intent, RouteDecision
from support_assistant.observability import meter
from support_assistant.tools.orders import Clock, utc_now

_store_failures = meter(__name__).create_counter("handoff.store.failures")

IST = timezone(timedelta(hours=5, minutes=30), "IST")
HUMAN_HOURS = (time(9, 0), time(21, 0))


class HandoffTicket(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticket_id: str
    session_id: str
    created_at: datetime
    customer_message: str
    route_rationale: str
    order_id: str | None = None
    caller_name: str | None = None  # from voice intake, so the human doesn't ask again


@runtime_checkable
class HandoffStore(Protocol):
    async def create(self, ticket: HandoffTicket) -> None: ...
    async def get(self, ticket_id: str) -> HandoffTicket | None: ...


class InMemoryHandoffStore:
    def __init__(self) -> None:
        self._tickets: dict[str, HandoffTicket] = {}

    async def create(self, ticket: HandoffTicket) -> None:
        self._tickets[ticket.ticket_id] = ticket

    async def get(self, ticket_id: str) -> HandoffTicket | None:
        return self._tickets.get(ticket_id)


class RedisHandoffStore:
    """Tickets as JSON strings plus a FIFO queue list the human-agent console consumes."""

    def __init__(self, redis: Redis, *, prefix: str = "sa:handoff:") -> None:
        self._r = redis
        self._prefix = prefix

    async def create(self, ticket: HandoffTicket) -> None:
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.set(self._prefix + ticket.ticket_id, ticket.model_dump_json())
            pipe.rpush(self._prefix + "queue", ticket.ticket_id)
            await pipe.execute()

    async def get(self, ticket_id: str) -> HandoffTicket | None:
        raw = await self._r.get(self._prefix + ticket_id)
        return HandoffTicket.model_validate_json(raw) if raw else None


class EscalationHandler(Handler):
    name = "escalation"
    intents: ClassVar[tuple[Intent, ...]] = (Intent.ESCALATE_TO_HUMAN,)
    description = (
        "the customer asks for a human, is upset or complaining, wants a policy exception or "
        "a refund approved, or the request fits no other intent"
    )

    def __init__(self, store: HandoffStore, clock: Clock = utc_now) -> None:
        self._store = store
        self._clock = clock

    async def handle(self, msg: CheckedMessage, route: RouteDecision) -> DraftReply:
        now = self._clock()
        ticket = HandoffTicket(
            ticket_id=f"HT-{uuid.uuid4().hex[:8].upper()}",
            session_id=msg.session_id,
            created_at=now,
            customer_message=msg.text[:2000],
            route_rationale=route.rationale,
            order_id=route.order_id,
            caller_name=msg.raw.caller.name if msg.raw.caller else None,
        )
        try:
            await self._store.create(ticket)
        except Exception:
            # The safest path must not be the one that fails: if the handoff store is down,
            # still tell the customer how to reach a person instead of a generic error.
            _store_failures.add(1)
            return DraftReply(
                text=(
                    "I want to get you to a person, but I couldn't file the request just now. "
                    "Please write to us again in a few minutes, or reach our team "
                    "9:00–21:00 IST, every day."
                ),
                intent=route.intent,
                handler=self.name,
                needs_human=True,
            )

        local = now.astimezone(IST).time()
        if HUMAN_HOURS[0] <= local < HUMAN_HOURS[1]:
            when = "A member of our team will pick it up shortly."
        else:
            when = "Our team is offline right now; it is queued for when they are back at 9:00 IST."
        text = (
            f"I've passed this to our support team, ticket {ticket.ticket_id}. "
            "Human agents are available 9:00–21:00 IST, every day. "
            f"{when}"
        )
        return DraftReply(text=text, intent=route.intent, handler=self.name, needs_human=True)
