"""Deterministic-layer metrics on the PulseNest adversarial sets (no LLM).

Input side: InputGuardrail with the LLM down, i.e. the real outage policy. This is the honest
floor: what the guard catches when the classifier is unavailable.
Output side: OutputGuardrail with the rubric disabled, i.e. the structural check alone.
"""

import json
from pathlib import Path

from guard_helpers import checked

from support_assistant.contracts import (
    BlockedMessage,
    ChatTurn,
    DraftReply,
    Intent,
    Passage,
    RawMessage,
    ToolResult,
)
from support_assistant.guardrails import InputGuardrail, OutputGuardrail
from support_assistant.llm import FakeLLM

DATA = Path(__file__).resolve().parents[2] / "eval" / "data"


def _rows(name: str) -> list[dict]:
    return [json.loads(line) for line in (DATA / name).read_text().splitlines() if line.strip()]


async def test_input_set_heuristic_only_recall_and_fpr():
    rows = _rows("adversarial_domain.jsonl")
    assert len(rows) >= 30
    guard = InputGuardrail(FakeLLM(fail=True))
    tp = fn = fp = tn = 0
    misses, false_pos = [], []
    for r in rows:
        history = tuple(ChatTurn(**t) for t in r["history"])
        out = await guard.check(RawMessage(session_id=r["id"], text=r["text"], history=history))
        blocked = isinstance(out, BlockedMessage)
        if r["expected"] == "block":
            tp, fn = tp + blocked, fn + (not blocked)
            if not blocked:
                misses.append(r["id"])
        else:
            fp, tn = fp + blocked, tn + (not blocked)
            if blocked:
                false_pos.append(r["id"])
    recall, fpr = tp / (tp + fn), fp / (fp + tn)
    print(
        f"\nheuristic-only input guard: recall={recall:.2f} ({tp}/{tp + fn}) "
        f"FPR={fpr:.2f} ({fp}/{fp + tn}) misses={misses} false_positives={false_pos}"
    )
    # Known misses are documented in docs/guardrail_trace.md; the LLM layer is meant for them.
    assert set(misses) <= {"a20", "a21", "a22", "a24"}
    assert set(false_pos) <= {"b14"}


def _draft(row: dict) -> DraftReply:
    ev = row["evidence"]
    return DraftReply(
        text=row["draft_text"],
        intent=Intent.RETURN_REFUND,
        handler="eval",
        tool_results=tuple(ToolResult(**t) for t in ev.get("tool_results", [])),
        passages=tuple(
            Passage(
                doc_id=p["doc_id"],
                chunk_id=p["doc_id"],
                title=p["doc_id"],
                text=p["text"],
                score=1.0,
            )
            for p in ev.get("passages", [])
        ),
    )


async def test_output_set_structural_only():
    rows = _rows("adversarial_output.jsonl")
    guard = OutputGuardrail(FakeLLM(), use_rubric=False)
    correct, wrong = 0, []
    for r in rows:
        msg = checked(r["evidence"].get("user_text", "Can I return PN-10021?"))
        out = await guard.check(_draft(r), msg)
        got = "rewrite" if out.rewritten else "pass"
        if got == r["expected"]:
            correct += 1
        else:
            wrong.append(r["id"])
    print(f"\nstructural-only output guard: {correct}/{len(rows)} correct, wrong={wrong}")
    known = {r["id"] for r in rows if r["known_structural_miss"]}
    assert set(wrong) <= known
