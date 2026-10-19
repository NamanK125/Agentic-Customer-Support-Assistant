"""LLM intent router with schema validation, confidence gating, caching and a degraded mode.

The prompt and the JSON-schema `intent` enum are generated from the HandlerRegistry, so the router
never names a specific agent. Order ids are taken from a regex over the conversation; the LLM's
guess is accepted only if the customer actually wrote those digits (LLMs hallucinate ids).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

from opentelemetry import trace
from pydantic import BaseModel, Field, ValidationError

from support_assistant.contracts import ChatTurn, CheckedMessage, Intent, RouteDecision
from support_assistant.harness.cache import CacheStats, ClassifierCache, make_cache_key
from support_assistant.harness.llm_io import UNTRUSTED_NOTE, ChatModel, fence
from support_assistant.harness.registry import HandlerRegistry
from support_assistant.llm import LLMError, parse_json
from support_assistant.observability import tracer
from support_assistant.tools.orders import find_order_ids, normalize_order_id

_tracer = tracer(__name__)

PROMPT_VERSION = "router-v1"
DEGRADED_CONFIDENCE = 0.6

# Degraded-mode rules, checked in this order; first registered match wins.
_RULES: tuple[tuple[Intent, re.Pattern[str]], ...] = (
    (Intent.ESCALATE_TO_HUMAN, re.compile(
        r"\b(human|agent|person|representative|manager|supervisor|complain\w*|"
        r"speak to|talk to|call me|escalat\w*)\b", re.I)),
    (Intent.RETURN_REFUND, re.compile(
        r"\b(return\w*|refund\w*|money back|send (it )?back|exchange|restocking)\b", re.I)),
    (Intent.ORDER_STATUS, re.compile(
        r"\b(where is|where's|track\w*|deliver\w*|ship\w*|dispatch\w*|arriv\w*|eta|"
        r"order status|my order)\b", re.I)),
    (Intent.PRODUCT_QUESTION, re.compile(
        r"(\b(bp-200|ox-02|th-01|sc-03|cuff|oximeter|thermometer|scale|batter(y|ies)|"
        r"error|e[1-5]|pair\w*|bluetooth|app|warranty|calibrat\w*|accura\w*|reading)\b|"
        r"care\+|\bhow (do|to|can|does)\b)", re.I)),
)  # fmt: skip


class ClassifierOutput(BaseModel):
    intent: str
    confidence: float = Field(ge=0.0, le=1.0)
    order_id: str | None = None
    rationale: str = ""


class LLMRouter:
    def __init__(
        self,
        llm: ChatModel,
        registry: HandlerRegistry,
        *,
        cache: ClassifierCache | None = None,
        threshold: float = 0.5,
        model_id: str = "",
        timeout_s: float | None = None,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self.cache = cache
        self._threshold = threshold
        self._model_id = model_id
        self._timeout_s = timeout_s

    # ---- prompt ---------------------------------------------------------------------------

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": [i.value for i in self._registry.intents]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "order_id": {"type": ["string", "null"]},
            },
            # No free-text rationale: decode costs ~100 ms/token on the in-house server, and a
            # sentence nobody acts on was ~3 s of every turn. RouteDecision.rationale is filled
            # from what the router actually did (threshold, regex, degraded path).
            "required": ["intent", "confidence", "order_id"],
            "additionalProperties": False,
        }

    def system_prompt(self) -> str:
        intents = "\n".join(f"- {i.value}: {d}" for i, d in self._registry.descriptions().items())
        fallback = (
            f"choose {Intent.ESCALATE_TO_HUMAN.value}"
            if Intent.ESCALATE_TO_HUMAN in self._registry
            else "give a low confidence"
        )
        return (
            "You are the intent router for PulseNest customer support (home health devices: "
            "BP-200 blood pressure monitor, OX-02 pulse oximeter, TH-01 thermometer, SC-03 smart "
            "scale, Care+ app subscription).\n"
            "Classify the customer's latest message into exactly one intent:\n"
            f"{intents}\n\n"
            "Rules:\n"
            f"- If no intent clearly fits, or the customer asks for a person, {fallback}.\n"
            "- Use the previous assistant turn only to resolve short replies like 'yes' or "
            "'that one'.\n"
            "- order_id: copy an order id (format PN-12345) only if the customer wrote it; "
            "otherwise null. Never invent one.\n"
            "- confidence: your probability (0 to 1) that the intent is correct.\n"
            f"- {UNTRUSTED_NOTE}\n\n"
            'Reply with only a JSON object: {"intent": "...", "confidence": 0.0, '
            '"order_id": null}'
        )

    def prompt_id(self) -> str:
        """Version + hash of the rendered prompt: registering a handler (or editing a
        description) changes the prompt, which must invalidate cached classifications."""
        digest = hashlib.sha256(self.system_prompt().encode()).hexdigest()[:12]
        return f"{PROMPT_VERSION}:{digest}"

    def _messages(self, text: str, context: str) -> list[dict[str, Any]]:
        user = fence("user_message", text)
        if context:
            user = fence("previous_assistant_turn", context) + "\n" + user
        return [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": user},
        ]

    # ---- routing --------------------------------------------------------------------------

    async def route(self, msg: CheckedMessage) -> RouteDecision:
        with _tracer.start_as_current_span("router.route") as span:
            history = msg.raw.history
            context = next((t.text for t in reversed(history) if t.role == "assistant"), "")
            key = make_cache_key(
                msg.text, context=context, model_id=self._model_id, prompt_version=self.prompt_id()
            )
            out, cached = await self._cached(key), False
            if out is not None:
                cached = True
            else:
                try:
                    result = await asyncio.wait_for(
                        self._llm.chat(
                            self._messages(msg.text, context),
                            role="router",
                            json_schema=self.json_schema(),
                            max_tokens=200,
                        ),
                        self._timeout_s,
                    )
                except (LLMError, TimeoutError) as exc:
                    span.record_exception(exc)
                    decision = self._degraded(msg.text, history, type(exc).__name__)
                    self._annotate(span, decision, degraded=True)
                    return decision
                try:
                    out = ClassifierOutput.model_validate(parse_json(result.content))
                except (ValueError, ValidationError) as exc:
                    span.add_event("router.invalid_output", {"error": str(exc)[:200]})
                    out = None
                if out is not None and self.cache is not None and self._known(out.intent):
                    await self.cache.set(key, out.model_dump_json())

            decision = self._decide(out, msg.text, history, cached=cached)
            self._annotate(span, decision, degraded=False)
            return decision

    async def _cached(self, key: str) -> ClassifierOutput | None:
        if self.cache is None:
            return None
        raw = await self.cache.get(key)
        if raw is None:
            return None
        try:
            return ClassifierOutput.model_validate_json(raw)
        except ValidationError:
            return None

    def _known(self, intent: str) -> bool:
        return intent in {i.value for i in self._registry.intents}

    def _decide(
        self,
        out: ClassifierOutput | None,
        text: str,
        history: tuple[ChatTurn, ...],
        *,
        cached: bool,
    ) -> RouteDecision:
        if out is None:
            return self._escalate(
                "invalid classifier output", 0.0, self._order_id(text, history, None), cached
            )
        order_id = self._order_id(text, history, out.order_id)
        if not self._known(out.intent):
            return self._escalate(
                f"unknown intent {out.intent!r}", out.confidence, order_id, cached
            )
        if out.confidence < self._threshold:
            return self._escalate(
                f"low confidence {out.confidence:.2f} for {out.intent}: {out.rationale}",
                out.confidence, order_id, cached,
            )  # fmt: skip
        return RouteDecision(
            intent=Intent(out.intent), confidence=out.confidence, order_id=order_id,
            cached=cached, rationale=out.rationale,
        )  # fmt: skip

    def _escalate(self, why: str, conf: float, order_id: str | None, cached: bool) -> RouteDecision:
        return RouteDecision(
            intent=Intent.ESCALATE_TO_HUMAN, confidence=conf, order_id=order_id,
            cached=cached, rationale=f"escalated: {why}",
        )  # fmt: skip

    @staticmethod
    def _order_id(text: str, history: tuple[ChatTurn, ...], llm_guess: str | None) -> str | None:
        """Regex hit in the current message > most recent id the customer wrote earlier >
        the LLM's guess, only if its digits appear in what the customer wrote."""
        if ids := find_order_ids(text):
            return ids[0]
        user_text = [t.text for t in history if t.role == "user"]
        for turn in reversed(user_text):
            if ids := find_order_ids(turn):
                return ids[-1]
        guess = normalize_order_id(llm_guess or "")
        if guess and any(guess[3:] in t for t in [text, *user_text]):
            return guess
        return None

    def _degraded(self, text: str, history: tuple[ChatTurn, ...], error: str) -> RouteDecision:
        order_id = self._order_id(text, history, None)
        intent = next(
            (i for i, rx in _RULES if i in self._registry and rx.search(text)),
            Intent.ORDER_STATUS if order_id and Intent.ORDER_STATUS in self._registry else None,
        )
        why = f"degraded: LLM unavailable ({error}), rule-based fallback"
        if intent is None:
            return self._escalate(f"{why}, no rule matched", DEGRADED_CONFIDENCE, order_id, False)
        return RouteDecision(
            intent=intent, confidence=DEGRADED_CONFIDENCE, order_id=order_id, rationale=why
        )

    @staticmethod
    def _annotate(span: trace.Span, d: RouteDecision, *, degraded: bool) -> None:
        span.set_attribute("app.intent", d.intent.value)
        span.set_attribute("app.router.confidence", d.confidence)
        span.set_attribute("app.cache_hit", d.cached)
        span.set_attribute("app.router.degraded", degraded)
        if d.rationale.startswith("escalated"):
            span.set_attribute("app.router.escalated_reason", d.rationale[:200])

    async def cache_stats(self) -> CacheStats | None:
        return await self.cache.stats() if self.cache is not None else None
