"""Provider-agnostic async LLM client for any OpenAI-compatible endpoint (vLLM, Groq, ...).

- Per-role model selection (router / guard / agent), see Settings.model_for.
- Timeouts on every call; retry with backoff on transient errors; key rotation on 401/429.
- Structured output via json_schema / json_object / prompt-only, per LLM_STRUCTURED_OUTPUT.
- One OTel span per call with GenAI semantic-convention attributes.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import openai
from openai import AsyncOpenAI
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from support_assistant.config import ModelRole, Settings, get_settings
from support_assistant.observability import meter, tracer

_tracer = tracer(__name__)
_meter = meter(__name__)
_latency = _meter.create_histogram("llm.request.duration", unit="ms")
_errors = _meter.create_counter("llm.request.errors")

_TRANSIENT = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
    openai.RateLimitError,
)


_CLIENT_ERRORS = (
    openai.BadRequestError,
    openai.NotFoundError,
    openai.UnprocessableEntityError,
)
_THINK = re.compile(r"^.*?</think>\s*", re.DOTALL)


class LLMError(RuntimeError):
    """Raised when the LLM is unavailable after retries/key rotation. Callers must degrade."""


class ChatResult(BaseModel):
    content: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)  # {id, name, arguments: dict}
    model: str = ""
    latency_ms: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None


def parse_json(text: str) -> dict[str, Any]:
    """Tolerant JSON-object parse: strips code fences and surrounding prose."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


class CircuitBreaker:
    """Opens after `threshold` consecutive failed calls; while open, calls are refused for
    `cooldown_s`, then one trial call is let through (half-open) to probe for recovery."""

    def __init__(self, threshold: int, cooldown_s: float, clock=time.monotonic) -> None:
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        if self._trial_in_flight or self._clock() - self._opened_at < self._cooldown_s:
            return False
        self._trial_in_flight = True  # half-open: exactly one probe
        return True

    def record(self, *, ok: bool) -> None:
        self._trial_in_flight = False
        if ok:
            self._failures = 0
            self._opened_at = None
            return
        self._failures += 1
        if self._opened_at is not None or self._failures >= self._threshold:
            self._opened_at = self._clock()  # (re)open; a failed probe restarts the cooldown


class LLMClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self._keys = self._s.api_keys()
        self._key_idx = 0
        self._clients: dict[int, AsyncOpenAI] = {}
        # Bounded in-flight calls per role: the model server is the shared bottleneck, so excess
        # turns queue here (and hit their turn budget) instead of piling onto the server.
        self._slots: dict[str, asyncio.Semaphore] = {
            r: asyncio.Semaphore(self._s.llm_max_concurrency) for r in ("router", "guard", "agent")
        }
        self._breaker = CircuitBreaker(
            self._s.llm_circuit_failures, self._s.llm_circuit_cooldown_s
        )

    def _client(self) -> AsyncOpenAI:
        if self._key_idx not in self._clients:
            self._clients[self._key_idx] = AsyncOpenAI(
                base_url=self._s.llm_base_url,
                api_key=self._keys[self._key_idx],
                timeout=self._s.llm_timeout_s,
                max_retries=0,  # retries are ours, so they are visible in traces
            )
        return self._clients[self._key_idx]

    def _rotate_key(self) -> bool:
        if len(self._keys) <= 1:
            return False
        self._key_idx = (self._key_idx + 1) % len(self._keys)
        return True

    def _response_format(self, json_schema: dict[str, Any] | None) -> dict[str, Any] | None:
        if json_schema is None or self._s.llm_structured_output == "none":
            return None
        if self._s.llm_structured_output == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": json_schema, "strict": True},
        }

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        role: ModelRole = "agent",
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> ChatResult:
        model = self._s.model_for(role)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if (rf := self._response_format(json_schema)) is not None:
            kwargs["response_format"] = rf
        if self._s.llm_disable_thinking:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

        with _tracer.start_as_current_span(f"chat {model}") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.model", model)
            span.set_attribute("gen_ai.request.temperature", temperature)
            span.set_attribute("app.llm.role", role)
            span.set_attribute("server.address", self._s.llm_base_url)
            start = time.perf_counter()
            if not self._breaker.allow():
                # Fail fast: callers have deterministic fallbacks, so during an outage a turn
                # should reach them in microseconds, not after seconds of retries per call.
                _errors.add(1, {"role": role, "error": "CircuitOpen"})
                span.set_attribute("app.llm.circuit", "open")
                span.set_status(Status(StatusCode.ERROR, "circuit open"))
                raise LLMError(f"LLM circuit open ({role}, {model}); skipping call")
            try:
                async with self._slots[role]:
                    span.set_attribute(
                        "app.llm.queue_wait_ms", (time.perf_counter() - start) * 1000
                    )
                    resp = await self._call_with_retries(kwargs, span)
                self._breaker.record(ok=True)
            except Exception as exc:
                # A 4xx means our request was wrong (e.g. tools sent to a server without a tool
                # parser), not that the server is down, so it must not trip the breaker for
                # every other caller.
                self._breaker.record(ok=isinstance(exc, _CLIENT_ERRORS))
                _errors.add(1, {"role": role, "error": type(exc).__name__})
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise LLMError(f"LLM call failed ({role}, {model}): {exc}") from exc
            elapsed_ms = (time.perf_counter() - start) * 1000
            _latency.record(elapsed_ms, {"role": role, "model": model})

            choice = resp.choices[0].message
            tool_calls = []
            for tc in choice.tool_calls or []:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"_unparsed": tc.function.arguments}
                tool_calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})

            usage = resp.usage
            if usage is not None:
                span.set_attribute("gen_ai.usage.input_tokens", usage.prompt_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", usage.completion_tokens)
            span.set_attribute("gen_ai.response.model", resp.model or model)
            return ChatResult(
                # Defensive: a thinking model whose template ignores enable_thinking still must
                # not leak its reasoning to the customer.
                content=_THINK.sub("", choice.content or "", count=1),
                tool_calls=tool_calls,
                model=resp.model or model,
                latency_ms=elapsed_ms,
                input_tokens=usage.prompt_tokens if usage else None,
                output_tokens=usage.completion_tokens if usage else None,
            )

    async def _call_with_retries(self, kwargs: dict[str, Any], span: Any) -> Any:
        attempt_no = 0
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._s.llm_max_retries + 1),
            wait=wait_exponential_jitter(multiplier=0.25, max=4.0),
            retry=retry_if_exception_type((*_TRANSIENT, openai.AuthenticationError)),
            reraise=True,
        ):
            with attempt:
                attempt_no += 1
                span.set_attribute("app.llm.attempts", attempt_no)
                try:
                    return await self._client().chat.completions.create(**kwargs)
                except (openai.RateLimitError, openai.AuthenticationError) as exc:
                    span.add_event("llm.retry", {"error": type(exc).__name__})
                    if not self._rotate_key() and isinstance(exc, openai.AuthenticationError):
                        msg = "authentication failed and no other key to rotate to"
                        raise LLMError(msg) from exc
                    raise
                except _TRANSIENT as exc:
                    span.add_event("llm.retry", {"error": type(exc).__name__})
                    raise
        raise LLMError("unreachable")
