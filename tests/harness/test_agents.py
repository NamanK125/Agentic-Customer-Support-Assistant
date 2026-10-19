"""Tool agent (native function calling, bounded, allowlisted, degraded) and escalation handler."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from support_assistant.agents import EscalationHandler, InMemoryHandoffStore, ToolAgent
from support_assistant.contracts import Intent, RouteDecision
from support_assistant.llm import ChatResult, FakeLLM, LLMError
from support_assistant.tools import CHECK_RETURN_ELIGIBILITY, GET_ORDER_STATUS

from .helpers import checked, fixed_clock, scripted, tool_call


def route(intent: Intent, order_id: str | None = "PN-10004") -> RouteDecision:
    return RouteDecision(intent=intent, confidence=0.9, order_id=order_id)


async def test_native_tool_call_then_answer(order_tools):
    llm = FakeLLM(
        scripted(
            tool_call(CHECK_RETURN_ELIGIBILITY, order_id="PN-10004", opened=True),
            ChatResult(content="Yes, you can return it until 2026-10-24 with a 10% fee."),
        )
    )
    draft = await ToolAgent(llm, order_tools)(
        checked("can I return PN-10004? I opened it"), route(Intent.RETURN_REFUND)
    )
    assert draft.text.startswith("Yes, you can return it")
    assert [r.tool for r in draft.tool_results] == [CHECK_RETURN_ELIGIBILITY]
    assert draft.tool_results[0].data["eligible"] is True
    # Second call carries the tool result back to the model with confirmed/unconfirmed split.
    tool_msg = llm.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
    payload = json.loads(tool_msg["content"])
    assert "refund_estimate_inr" in payload["unconfirmed_fields"]
    assert [t["function"]["name"] for t in llm.calls[0]["tools"]] == [
        CHECK_RETURN_ELIGIBILITY,
        GET_ORDER_STATUS,
    ]


async def test_order_status_allowlist_excludes_return_tool(order_tools):
    llm = FakeLLM(
        scripted(tool_call(CHECK_RETURN_ELIGIBILITY, order_id="PN-10004"), ChatResult(content="ok"))
    )
    draft = await ToolAgent(llm, order_tools)(
        checked("where is PN-10004"), route(Intent.ORDER_STATUS)
    )
    assert [t["function"]["name"] for t in llm.calls[0]["tools"]] == [GET_ORDER_STATUS]
    assert not draft.tool_results[0].ok and "unknown tool" in draft.tool_results[0].error


async def test_no_order_id_asks_without_calling_anything(order_tools):
    llm = FakeLLM()
    draft = await ToolAgent(llm, order_tools)(
        checked("where's my order?"), route(Intent.ORDER_STATUS, None)
    )
    assert "order ID" in draft.text and draft.tool_results == () and llm.calls == []


async def test_model_skipping_tools_triggers_deterministic_call(order_tools):
    llm = FakeLLM(
        scripted(
            ChatResult(content="It will arrive tomorrow!"), ChatResult(content="It's shipped.")
        )
    )
    draft = await ToolAgent(llm, order_tools)(
        checked("where is PN-10002"), route(Intent.ORDER_STATUS, "PN-10002")
    )
    assert [r.tool for r in draft.tool_results] == [GET_ORDER_STATUS]
    assert draft.text == "It's shipped."  # the un-grounded first answer was discarded
    assert "tools" not in llm.calls[1] or not llm.calls[1].get("tools")


async def test_unknown_tool_and_bad_args_become_error_results(order_tools):
    llm = FakeLLM(
        scripted(
            ChatResult(
                tool_calls=[
                    {"id": "a", "name": "issue_refund", "arguments": {"order_id": "PN-10004"}},
                    {"id": "b", "name": GET_ORDER_STATUS, "arguments": {"_unparsed": "{bad"}},
                ]
            ),
            ChatResult(content="Sorry, I could not do that."),
        )
    )
    draft = await ToolAgent(llm, order_tools)(
        checked("refund PN-10004 now"), route(Intent.RETURN_REFUND)
    )
    results = {r.tool: r for r in draft.tool_results}
    assert not results["issue_refund"].ok
    # Missing order_id is filled with the customer's id, so the lookup itself succeeds.
    assert results[GET_ORDER_STATUS].ok


async def test_tool_call_cannot_target_another_order(order_tools):
    llm = FakeLLM(
        scripted(tool_call(GET_ORDER_STATUS, order_id="PN-10003"), ChatResult(content="x"))
    )
    draft = await ToolAgent(llm, order_tools)(
        checked("status of PN-10004"), route(Intent.ORDER_STATUS)
    )
    assert (
        not draft.tool_results[0].ok
        and "not provided by the customer" in draft.tool_results[0].error
    )


async def test_loop_is_bounded(order_tools):
    llm = FakeLLM(scripted(tool_call(GET_ORDER_STATUS, order_id="PN-10004")))  # always calls tools
    llm_final = ChatResult(content="final")

    def respond(messages, kwargs):
        return (
            llm_final
            if not kwargs.get("tools")
            else tool_call(GET_ORDER_STATUS, order_id="PN-10004")
        )

    llm = FakeLLM(respond)
    draft = await ToolAgent(llm, order_tools, max_rounds=3)(
        checked("PN-10004"), route(Intent.ORDER_STATUS)
    )
    assert len(llm.calls) == 4  # 3 tool rounds + 1 forced final answer without tools
    assert len(draft.tool_results) == 3 and draft.text == "final"


async def test_llm_outage_renders_template_from_confirmed_fields(order_tools):
    draft = await ToolAgent(FakeLLM(fail=True), order_tools)(
        checked("can I return PN-10004?"), route(Intent.RETURN_REFUND)
    )
    assert [r.tool for r in draft.tool_results] == [CHECK_RETURN_ELIGIBILITY]
    assert "eligible for return until 2026-10-24" in draft.text
    assert "3149" not in draft.text and "₹" not in draft.text  # unconfirmed estimate never stated
    assert "after the item passes inspection" in draft.text


async def test_llm_outage_mid_loop_keeps_results(order_tools):
    calls = {"n": 0}

    def respond(_m, _k):
        calls["n"] += 1
        if calls["n"] > 1:
            raise LLMError("down")
        return tool_call(GET_ORDER_STATUS, order_id="PN-10001")

    draft = await ToolAgent(FakeLLM(respond), order_tools)(
        checked("PN-10001?"), route(Intent.ORDER_STATUS, "PN-10001")
    )
    assert len(draft.tool_results) == 1
    assert "processing" in draft.text and "only once the order ships" in draft.text


async def test_template_states_eta_only_when_confirmed(order_tools):
    agent = ToolAgent(FakeLLM(fail=True), order_tools)
    shipped = await agent(checked("PN-10002"), route(Intent.ORDER_STATUS, "PN-10002"))
    assert "2026-10-21" in shipped.text
    missing = await agent(checked("PN-12345"), route(Intent.ORDER_STATUS, "PN-12345"))
    assert "couldn't find" in missing.text


async def test_escalation_creates_ticket_and_mentions_hours():
    store = InMemoryHandoffStore()
    handler = EscalationHandler(store, clock=fixed_clock)
    draft = await handler(
        checked("I want a manager"),
        RouteDecision(intent=Intent.ESCALATE_TO_HUMAN, confidence=0.9, rationale="asked"),
    )
    assert draft.needs_human and "9:00–21:00 IST" in draft.text
    ticket_id = draft.text.split("ticket ")[1].split(".")[0]
    ticket = await store.get(ticket_id)
    assert ticket is not None and ticket.customer_message == "I want a manager"
    assert "shortly" in draft.text  # 11:30 IST is inside hours


async def test_escalation_outside_hours_says_queued():
    night = lambda: datetime(2026, 10, 19, 18, 0, tzinfo=UTC)  # noqa: E731 - 23:30 IST
    draft = await EscalationHandler(InMemoryHandoffStore(), clock=night)(
        checked("help"), RouteDecision(intent=Intent.ESCALATE_TO_HUMAN, confidence=0.0)
    )
    assert "queued" in draft.text
