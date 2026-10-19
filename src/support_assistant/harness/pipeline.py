"""The only orchestration path for a turn:

    raw -> input guard -> (blocked: reply, stop) -> router -> handler -> output guard -> reply

Guarantees, enforced here rather than assumed of the components:
- a BlockedMessage (or anything that is not a CheckedMessage) never reaches the router/handlers;
- the reply text is either the output guard's FinalReply or a fixed safe constant, never a draft
  that skipped the output guard;
- `handle` never raises (except cancellation); every stage runs under the per-turn time budget,
  and a failed or slow stage degrades to a safe reply recorded on the span.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal, TypeVar

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from support_assistant.contracts import (
    BlockedMessage,
    CallerProfile,
    ChatTurn,
    CheckedMessage,
    DraftReply,
    FinalReply,
    InputGuard,
    Intent,
    OutputGuard,
    RawMessage,
    RouteDecision,
    Router,
)
from support_assistant.harness.cache import CacheStats
from support_assistant.harness.registry import HandlerRegistry
from support_assistant.harness.session import SessionStore
from support_assistant.observability import meter, tracer

_tracer = tracer(__name__)
_meter = meter(__name__)
_stage_ms = _meter.create_histogram("support.stage.duration", unit="ms")
_turn_ms = _meter.create_histogram("support.turn.duration", unit="ms")
_turns = _meter.create_counter("support.turns")
_blocked = _meter.create_counter("support.turns.blocked")
_rewritten = _meter.create_counter("support.turns.rewritten")
_failed = _meter.create_counter("support.turns.failed")

T = TypeVar("T")

SAFE_FALLBACK = (
    "Sorry, I couldn't complete that just now. Please try again in a moment, or ask me to "
    "connect you with a human agent (available 9:00–21:00 IST, every day)."
)


class StageError(Exception):
    def __init__(self, stage: str, cause: BaseException) -> None:
        super().__init__(f"{stage} failed: {type(cause).__name__}: {cause}")
        self.stage = stage


class SupportPipeline:
    def __init__(
        self,
        input_guard: InputGuard,
        router: Router,
        registry: HandlerRegistry,
        output_guard: OutputGuard,
        sessions: SessionStore,
        *,
        turn_timeout_s: float = 30.0,
        output_guard_reserve_s: float = 5.0,
        router_timeout_s: float | None = None,
    ) -> None:
        self.input_guard = input_guard
        self.router = router
        self.registry = registry
        self.output_guard = output_guard
        self.sessions = sessions
        self._turn_timeout_s = turn_timeout_s
        self._guard_reserve_s = min(output_guard_reserve_s, turn_timeout_s / 2)
        # The router gets its own cap so that, if it is slow, the escalation fallback still has
        # budget left to run.
        self._router_timeout_s = router_timeout_s or turn_timeout_s / 3

    async def cache_stats(self) -> CacheStats | None:
        stats = getattr(self.router, "cache_stats", None)
        return await stats() if stats is not None else None

    async def handle(
        self,
        session_id: str,
        text: str,
        channel: Literal["text", "voice"] = "text",
        caller: CallerProfile | None = None,
    ) -> FinalReply:
        start = time.perf_counter()
        deadline = asyncio.get_running_loop().time() + self._turn_timeout_s
        with _tracer.start_as_current_span("support.turn") as span:
            trace_id = format(span.get_span_context().trace_id, "032x")
            span.set_attribute("app.channel", channel)
            try:
                reply = await self._turn(span, session_id, text, channel, deadline, caller)
            except Exception as exc:  # last line of defence: never raise to the caller
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                stage = exc.stage if isinstance(exc, StageError) else "pipeline"
                span.set_attribute("app.failed_stage", stage)
                _failed.add(1, {"stage": stage})
                reply = FinalReply(
                    session_id=session_id, text=SAFE_FALLBACK, guard_notes=(f"error:{stage}",)
                )
            reply = reply.model_copy(update={"trace_id": trace_id})
            span.set_attribute("app.blocked", reply.blocked)
            span.set_attribute("app.rewritten", reply.rewritten)
            if reply.intent is not None:
                span.set_attribute("app.intent", reply.intent.value)
            if reply.handler is not None:
                span.set_attribute("app.handler", reply.handler)
            attrs = {"intent": reply.intent.value if reply.intent else "none", "channel": channel}
            _turns.add(1, attrs)
            if reply.blocked:
                _blocked.add(1, attrs)
            if reply.rewritten:
                _rewritten.add(1, attrs)
            _turn_ms.record((time.perf_counter() - start) * 1000, attrs)
            return reply

    async def _turn(
        self,
        span: trace.Span,
        session_id: str,
        text: str,
        channel: Literal["text", "voice"],
        deadline: float,
        caller: CallerProfile | None = None,
    ) -> FinalReply:
        loop = asyncio.get_running_loop()

        def budget(reserve: float = 0.0) -> float:
            return deadline - loop.time() - reserve

        r = self._guard_reserve_s
        try:
            history = await self._stage(
                "session.load", lambda: self.sessions.history(session_id), budget(r)
            )
        except StageError as exc:  # history is context, not a requirement
            span.add_event("session.load_failed", {"error": str(exc)})
            history = ()
        raw = RawMessage(
            session_id=session_id, text=text, channel=channel, history=history, caller=caller
        )

        # Fail closed: any guard error or unexpected return type stops the turn here.
        checked = await self._stage("input_guard", lambda: self.input_guard.check(raw), budget(r))
        if isinstance(checked, BlockedMessage):
            span.set_attribute("app.block_category", checked.verdict.category or "")
            return FinalReply(
                session_id=session_id, text=checked.safe_reply, blocked=True,
                guard_notes=(f"input:{checked.verdict.category or 'blocked'}",),
            )  # fmt: skip
        if not isinstance(checked, CheckedMessage):
            raise StageError("input_guard", TypeError(f"returned {type(checked).__name__}"))

        try:
            route: RouteDecision = await self._stage(
                "router",
                lambda: self.router.route(checked),
                min(self._router_timeout_s, budget(r)),
            )
        except StageError as exc:
            span.record_exception(exc)
            route = RouteDecision(
                intent=Intent.ESCALATE_TO_HUMAN, confidence=0.0, rationale=f"degraded: {exc}"
            )
        span.set_attribute("app.cache_hit", route.cached)
        span.set_attribute("app.route.rationale", route.rationale[:200])

        handler = self.registry.get(route.intent) or self.registry.get(Intent.ESCALATE_TO_HUMAN)
        if handler is None:
            raise StageError("handler", LookupError(f"no handler for {route.intent.value}"))
        span.set_attribute("app.handler", handler.name)
        draft: DraftReply = await self._stage("handler", lambda: handler(checked, route), budget(r))

        final = await self._stage(
            "output_guard", lambda: self.output_guard.check(draft, checked), budget()
        )
        if not isinstance(final, FinalReply):
            raise StageError("output_guard", TypeError(f"returned {type(final).__name__}"))
        final = final.model_copy(update={
            "session_id": session_id,
            "intent": final.intent or draft.intent,
            "handler": final.handler or draft.handler,
        })  # fmt: skip

        try:
            await self._stage(
                "session.save",
                lambda: self.sessions.append(
                    session_id,
                    ChatTurn(role="user", text=text),
                    ChatTurn(role="assistant", text=final.text),
                ),
                max(budget(), 1.0),
            )
        except StageError as exc:
            span.add_event("session.save_failed", {"error": str(exc)})
        return final

    async def _stage(self, name: str, fn: Callable[[], Awaitable[T]], timeout_s: float) -> T:
        start = time.perf_counter()
        outcome = "ok"
        with _tracer.start_as_current_span(f"support.{name}") as span:
            try:
                if timeout_s <= 0:
                    raise TimeoutError("turn budget exhausted")
                async with asyncio.timeout(timeout_s):
                    return await fn()
            except Exception as exc:
                outcome = "timeout" if isinstance(exc, TimeoutError) else "error"
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, outcome))
                raise StageError(name, exc) from exc
            finally:
                _stage_ms.record(
                    (time.perf_counter() - start) * 1000, {"stage": name, "outcome": outcome}
                )
