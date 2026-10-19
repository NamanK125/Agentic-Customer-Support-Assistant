"""Plain helpers shared by the harness tests (fixtures live in conftest.py)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from support_assistant import _seal  # tests may mint CheckedMessage; src may not
from support_assistant.contracts import ChatTurn, CheckedMessage, GuardVerdict, RawMessage
from support_assistant.llm import ChatResult

# 2026-10-19 11:30 IST: inside human support hours.
FIXED_NOW = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    return FIXED_NOW


def checked(
    text: str, *, session_id: str = "s1", history: tuple[ChatTurn, ...] = ()
) -> CheckedMessage:
    raw = RawMessage(session_id=session_id, text=text, history=history)
    return CheckedMessage(raw, GuardVerdict(allowed=True, checker="test"), seal=_seal.SEAL)


def router_reply(intent: str, confidence: float = 0.9, order_id: str | None = None) -> ChatResult:
    return ChatResult(
        content=json.dumps(
            {"intent": intent, "confidence": confidence, "order_id": order_id, "rationale": "test"}
        )
    )


def tool_call(name: str, call_id: str = "c1", **arguments: Any) -> ChatResult:
    return ChatResult(tool_calls=[{"id": call_id, "name": name, "arguments": arguments}])


def scripted(*results: ChatResult) -> Callable[[list[dict[str, Any]], dict[str, Any]], ChatResult]:
    """Responder returning `results` in order; the last one repeats."""
    queue = list(results)

    def respond(_messages: list[dict[str, Any]], _kwargs: dict[str, Any]) -> ChatResult:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return respond
