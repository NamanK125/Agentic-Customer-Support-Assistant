"""Shared builders for guardrail tests (kept out of conftest so test modules can import them)."""

from __future__ import annotations

import json
from typing import Any

from support_assistant import _seal
from support_assistant.contracts import (
    CheckedMessage,
    DraftReply,
    GuardVerdict,
    Intent,
    Passage,
    RawMessage,
    ToolResult,
)
from support_assistant.llm import ChatResult, FakeLLM


def classifier(verdict: str = "allow", category: str = "none", confidence: float = 0.9) -> FakeLLM:
    body = {"verdict": verdict, "category": category, "confidence": confidence, "reason": "test"}
    return FakeLLM(lambda _m, _k: ChatResult(content=json.dumps(body)))


def rubric(*violations: dict[str, Any]) -> FakeLLM:
    return FakeLLM(lambda _m, _k: ChatResult(content=json.dumps({"violations": list(violations)})))


def raw(text: str, history: tuple = ()) -> RawMessage:
    return RawMessage(session_id="s1", text=text, history=history)


def checked(text: str, checker: str = "llm", category: str | None = None) -> CheckedMessage:
    verdict = GuardVerdict(allowed=True, checker=checker, category=category)
    return CheckedMessage(raw(text), verdict, seal=_seal.SEAL)


ELIGIBILITY = ToolResult(
    tool="check_return_eligibility",
    args={"order_id": "PN-10021", "sku": "BP-200", "opened": True},
    ok=True,
    data={
        "eligible": True,
        "reason": "opened, not defective, within 15 days",
        "return_window_ends": "2026-11-02",
        "restocking_fee_pct": 10,
        "refund_estimate_inr": 3149,
    },
    confirmed_fields=("eligible", "reason", "return_window_ends", "restocking_fee_pct"),
)

REFUND_PASSAGE = Passage(
    doc_id="returns",
    chunk_id="returns-2",
    title="Refunds",
    text="Refunds are issued to the original payment method within 5–7 business days after the "
    "returned item passes inspection at the Bhiwandi warehouse.",
    score=0.8,
)


def draft(text: str, *tools: ToolResult, passages: tuple[Passage, ...] = ()) -> DraftReply:
    return DraftReply(
        text=text,
        intent=Intent.RETURN_REFUND,
        handler="tool_agent",
        tool_results=tools,
        passages=passages,
    )
