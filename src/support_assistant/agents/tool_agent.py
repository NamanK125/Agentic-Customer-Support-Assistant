"""Tool-calling agent for order_status and return_refund, using native (OpenAI-format) function
calling: structured, schema-validated arguments instead of parsing a ReAct transcript.

Safety properties, none of which depend on the model behaving:
- bounded loop (max_rounds LLM rounds, max_calls_per_round calls each);
- per-intent tool allowlist; unknown/disallowed tools and invalid args become error results;
- tool calls may only target the order id the customer gave (no hallucinated or injected ids);
- no order id -> ask for it, no tool call;
- model skips the tool -> the obvious tool is called deterministically;
- LLM outage -> a templated reply rendered from confirmed tool fields only.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from support_assistant.contracts import (
    CheckedMessage,
    DraftReply,
    Handler,
    Intent,
    RouteDecision,
    ToolResult,
)
from support_assistant.harness.llm_io import UNTRUSTED_NOTE, ChatModel, fence, fence_history
from support_assistant.llm import ChatResult, LLMError
from support_assistant.observability import tracer
from support_assistant.tools import (
    CHECK_RETURN_ELIGIBILITY,
    GET_ORDER_STATUS,
    OrderTools,
    normalize_order_id,
)

_tracer = tracer(__name__)

ASK_ORDER_ID = (
    "Could you share your order ID? You'll find it in your confirmation email or SMS; "
    "it looks like PN- followed by five digits."
)
REFUND_NOTE = (
    "The final refund amount is confirmed only after the item passes inspection at our "
    "Bhiwandi warehouse; the refund then reaches your original payment method within 5–7 "
    "business days."
)
_OPENED = re.compile(r"\b(opened|used|tried it|took it out)\b", re.I)
_UNOPENED = re.compile(r"\b(unopened|sealed|never opened|not opened|still in the box)\b", re.I)

SYSTEM_PROMPT = f"""You are PulseNest's order and returns assistant.
You can only look things up. You cannot issue refunds, approve exceptions, waive fees or change
orders; if asked, say a human agent can review it.

Use the tools, then answer from their results:
- State as facts only the fields listed in a result's "confirmed_fields".
- Never state a refund amount: refund_estimate_inr is unconfirmed. Say the final amount is
  confirmed after the item passes inspection, and refunds take 5–7 business days after that.
- Never give or guess a delivery date unless carrier_eta is in confirmed_fields.
- If a tool returns an error, say so plainly and ask for what is needed.
- Never give medical advice.
Answer in 2–4 short sentences.
{UNTRUSTED_NOTE}"""


class ToolAgent(Handler):
    name = "tool_agent"
    intents: ClassVar[tuple[Intent, ...]] = (Intent.ORDER_STATUS, Intent.RETURN_REFUND)
    description = "order lookups and return/refund eligibility for a specific order"
    intent_descriptions: ClassVar[dict[Intent, str]] = {
        Intent.ORDER_STATUS: "where an order is: status, shipping, tracking, delivery date",
        Intent.RETURN_REFUND: (
            "returning an item or getting a refund: eligibility, return window, restocking fee, "
            "refund timing"
        ),
    }
    _ALLOWED: ClassVar[dict[Intent, tuple[str, ...]]] = {
        Intent.ORDER_STATUS: (GET_ORDER_STATUS,),
        Intent.RETURN_REFUND: (CHECK_RETURN_ELIGIBILITY, GET_ORDER_STATUS),
    }

    def __init__(
        self,
        llm: ChatModel,
        tools: OrderTools,
        *,
        max_rounds: int = 3,
        max_calls_per_round: int = 3,
        history_turns: int = 4,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._max_rounds = max_rounds
        self._max_calls = max_calls_per_round
        self._history_turns = history_turns

    async def handle(self, msg: CheckedMessage, route: RouteDecision) -> DraftReply:
        intent = route.intent if route.intent in self._ALLOWED else Intent.ORDER_STATUS
        if route.order_id is None:
            return DraftReply(text=ASK_ORDER_ID, intent=intent, handler=self.name)

        results: list[ToolResult] = []
        with _tracer.start_as_current_span("tool_agent.run") as span:
            span.set_attribute("app.tool_agent.intent", intent.value)
            try:
                text = await self._run(msg, intent, route.order_id, results)
                span.set_attribute("app.degraded", False)
            except LLMError as exc:
                span.record_exception(exc)
                span.set_attribute("app.degraded", True)
                if not any(r.ok for r in results):
                    results.append(await self._obvious_call(msg, intent, route.order_id))
                text = render_template(results, route.order_id)
            span.set_attribute("app.tool_agent.calls", len(results))
            span.set_attribute("app.tool_agent.tools", [r.tool for r in results])
        return DraftReply(text=text, intent=intent, handler=self.name, tool_results=tuple(results))

    # ---- LLM loop -------------------------------------------------------------------------

    async def _run(
        self, msg: CheckedMessage, intent: Intent, order_id: str, results: list[ToolResult]
    ) -> str:
        allowed = self._ALLOWED[intent]
        messages = self._messages(msg, order_id)
        for _ in range(self._max_rounds):
            res = await self._llm.chat(
                messages,
                role="agent",
                tools=self._tools.schemas(allowed),
                tool_choice="auto",
                max_tokens=400,
            )
            if not res.tool_calls:
                if results:
                    return res.content.strip() or render_template(results, order_id)
                # Model answered without looking anything up: do the obvious lookup ourselves.
                result = await self._obvious_call(msg, intent, order_id)
                results.append(result)
                self._append_round(messages, "", [("fallback-0", result)])
                return await self._final(messages, results, order_id)

            executed = []
            for i, call in enumerate(res.tool_calls[: self._max_calls]):
                result = await self._execute(call, allowed, order_id)
                results.append(result)
                executed.append((str(call.get("id") or f"call-{len(results)}-{i}"), result))
            self._append_round(messages, res.content, executed)
        return await self._final(messages, results, order_id)

    async def _final(
        self, messages: list[dict[str, Any]], results: list[ToolResult], order_id: str
    ) -> str:
        res: ChatResult = await self._llm.chat(messages, role="agent", max_tokens=400)
        return res.content.strip() or render_template(results, order_id)

    def _messages(self, msg: CheckedMessage, order_id: str) -> list[dict[str, Any]]:
        history = msg.raw.history[-self._history_turns :] if self._history_turns else ()
        parts = [fence_history(history)] if history else []
        parts += [fence("user_message", msg.text), f"Order id from the customer: {order_id}"]
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(parts)},
        ]

    @staticmethod
    def _append_round(
        messages: list[dict[str, Any]], content: str, executed: list[tuple[str, ToolResult]]
    ) -> None:
        messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {"id": cid, "type": "function",
                 "function": {"name": r.tool, "arguments": json.dumps(r.args, default=str)}}
                for cid, r in executed
            ],
        })  # fmt: skip
        for cid, r in executed:
            messages.append({"role": "tool", "tool_call_id": cid, "content": _tool_payload(r)})

    async def _execute(
        self, call: dict[str, Any], allowed: tuple[str, ...], order_id: str
    ) -> ToolResult:
        name = str(call.get("name", ""))
        args = call.get("arguments")
        args = dict(args) if isinstance(args, dict) else {}
        args.setdefault("order_id", order_id)
        target = normalize_order_id(str(args["order_id"]))
        if target is not None and target != order_id:
            return ToolResult(
                tool=name, args=args, ok=False,
                error=f"order id {target} was not provided by the customer; use {order_id}",
            )  # fmt: skip
        return await self._tools.call(name, args, allowed=allowed)

    async def _obvious_call(self, msg: CheckedMessage, intent: Intent, order_id: str) -> ToolResult:
        if intent is Intent.RETURN_REFUND:
            opened = (
                False if _UNOPENED.search(msg.text) else True if _OPENED.search(msg.text) else None
            )
            return await self._tools.call(
                CHECK_RETURN_ELIGIBILITY, {"order_id": order_id, "opened": opened}
            )
        return await self._tools.call(GET_ORDER_STATUS, {"order_id": order_id})


def _tool_payload(r: ToolResult) -> str:
    unconfirmed = [k for k in r.data if k not in r.confirmed_fields]
    return json.dumps(
        {"ok": r.ok, "data": r.data, "confirmed_fields": list(r.confirmed_fields),
         "unconfirmed_fields": unconfirmed, "error": r.error},
        default=str,
    )  # fmt: skip


def render_template(results: list[ToolResult], order_id: str) -> str:
    """Deterministic reply built only from confirmed fields (used when the LLM is unavailable)."""
    latest: dict[str, ToolResult] = {r.tool: r for r in results if r.ok}
    if not latest:
        if any(r.error == "order_not_found" for r in results):
            return (
                f"I couldn't find an order with ID {order_id}. Could you double-check it? "
                "Order IDs look like PN- followed by five digits."
            )
        return (
            f"I couldn't look up order {order_id} right now. Please try again in a few minutes, "
            "or ask me to connect you with a human agent."
        )

    parts: list[str] = []
    if (r := latest.get(GET_ORDER_STATUS)) is not None:
        c = {k: r.data[k] for k in r.confirmed_fields}
        parts.append(
            f"Order {c.get('order_id', order_id)} is {c.get('status', 'being processed')}."
        )
        if "carrier" in c:
            tracking = f", tracking number {c['tracking_id']}" if "tracking_id" in c else ""
            parts.append(f"It is with {c['carrier']}{tracking}.")
        if "carrier_eta" in c:
            parts.append(f"The carrier's estimated delivery date is {c['carrier_eta']}.")
        elif "delivered_on" in c:
            parts.append(f"It was delivered on {c['delivered_on']}.")
        elif c.get("status") in ("processing", "shipped"):
            parts.append(
                "A delivery date is confirmed only once the order ships and the carrier "
                "issues an ETA."
            )
    if (r := latest.get(CHECK_RETURN_ELIGIBILITY)) is not None:
        c = {k: r.data[k] for k in r.confirmed_fields}
        item = f"The {c.get('sku', 'item')} from order {c.get('order_id', order_id)}"
        if c.get("eligible"):
            until = f" until {c['return_window_ends']}" if "return_window_ends" in c else ""
            parts.append(f"{item} is eligible for return{until}. {c.get('reason', '')}".strip())
            parts.append(REFUND_NOTE)
        else:
            parts.append(f"{item} is not eligible for return. {c.get('reason', '')}".strip())
    return " ".join(parts)
