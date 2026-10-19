"""LLM plumbing shared by both guards: nonce-delimited untrusted data and validated JSON output."""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from support_assistant.guardrails._text import neutralize_delimiters
from support_assistant.llm import ChatResult, LLMError, parse_json

M = TypeVar("M", bound=BaseModel)


class ChatLLM(Protocol):
    """What the guards need from LLMClient (FakeLLM satisfies it too)."""

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult: ...


class GuardLLMUnavailable(Exception):
    """The guard model could not give a usable answer: outage, or output that fails validation.
    Both are handled identically by the decision policies (see docs/guardrail_trace.md)."""


def new_nonce() -> str:
    return secrets.token_hex(8)


def wrap(label: str, text: str, nonce: str) -> str:
    return f'<data-{nonce} label="{label}">\n{neutralize_delimiters(text)}\n</data-{nonce}>'


async def ask_json(
    llm: ChatLLM,
    system: str,
    user: str,
    model: type[M],
    *,
    max_tokens: int = 300,
    timeout_s: float | None = None,
) -> M:
    """One guard-role call, validated. Outage, timeout and invalid output all raise
    GuardLLMUnavailable. The timeout is the guard's own and is shorter than the pipeline's stage
    budget, so a slow model degrades to the deterministic path instead of failing the turn."""
    schema = model.model_json_schema()
    try:
        async with asyncio.timeout(timeout_s):
            result = await llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                role="guard",
                json_schema=schema,
                temperature=0.0,
                max_tokens=max_tokens,
            )
    except TimeoutError as exc:
        raise GuardLLMUnavailable(f"timeout: after {timeout_s}s") from exc
    except LLMError as exc:
        raise GuardLLMUnavailable(f"llm_error: {exc}") from exc
    try:
        return model.model_validate(parse_json(result.content))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise GuardLLMUnavailable(f"invalid_output: {type(exc).__name__}") from exc
