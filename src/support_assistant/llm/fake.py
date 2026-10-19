"""Deterministic stand-in for LLMClient in unit tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from support_assistant.config import ModelRole
from support_assistant.llm.client import ChatResult, LLMError

Responder = Callable[[list[dict[str, Any]], dict[str, Any]], ChatResult | Awaitable[ChatResult]]


class FakeLLM:
    """Duck-types LLMClient.chat. `responder(messages, kwargs)` returns the ChatResult.

    Set `fail=True` to simulate an outage (raises LLMError). All calls are recorded in `.calls`.
    """

    def __init__(self, responder: Responder | None = None, *, fail: bool = False) -> None:
        self._responder = responder or (lambda _m, _k: ChatResult(content="{}"))
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self, messages: list[dict[str, Any]], *, role: ModelRole = "agent", **kwargs: Any
    ) -> ChatResult:
        kwargs["role"] = role
        self.calls.append({"messages": messages, **kwargs})
        if self.fail:
            raise LLMError("FakeLLM configured to fail")
        result = self._responder(messages, kwargs)
        if isinstance(result, Awaitable):
            result = await result
        return result
