"""Shared types and interfaces. Every component codes against these, never against each other.

Flow of one turn:

    RawMessage --InputGuard--> CheckedMessage | BlockedMessage
    CheckedMessage --Router--> RouteDecision
    (CheckedMessage, RouteDecision) --Handler--> DraftReply
    (DraftReply, CheckedMessage) --OutputGuard--> FinalReply

CheckedMessage can only be constructed with the seal from support_assistant._seal, which only
the guardrails package may import (import-linter contract in pyproject.toml). Handler.__call__
rejects anything that is not a CheckedMessage, so a handler cannot be reached with raw input.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from support_assistant import _seal


class Intent(StrEnum):
    PRODUCT_QUESTION = "product_question"
    ORDER_STATUS = "order_status"
    RETURN_REFUND = "return_refund"
    ESCALATE_TO_HUMAN = "escalate_to_human"


class ChatTurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["user", "assistant"]
    text: str


class CallerProfile(BaseModel):
    """Details the channel collected up front (voice intake asks for them on connect), so a turn
    doesn't need an extra "which order?" round trip. Both fields are format-constrained, so the
    profile can't carry free text or instructions; the name is never put into an LLM prompt.
    Caller-supplied, i.e. as trustworthy as the caller saying it, not identity-verified."""

    model_config = ConfigDict(frozen=True)

    # At most three words: enough for a name, too short to carry a sentence.
    name: str | None = Field(
        default=None, max_length=40, pattern=r"^[A-Za-z][A-Za-z.'-]*(?: [A-Za-z][A-Za-z.'-]*){0,2}$"
    )
    order_id: str | None = Field(default=None, pattern=r"^PN-\d{5}$")


class RawMessage(BaseModel):
    """Untrusted user input as it arrives from any channel."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    text: str
    channel: Literal["text", "voice"] = "text"
    history: tuple[ChatTurn, ...] = ()
    caller: CallerProfile | None = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GuardVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    checker: str  # which guard/stage produced the verdict
    category: str | None = None  # e.g. "prompt_injection", "unconfirmed_refund_amount"
    reason: str = ""
    score: float | None = None


class CheckedMessage:
    """A RawMessage that has passed the input guardrail. Immutable; mintable only with the seal."""

    __slots__ = ("_raw", "_verdict")

    def __init__(self, raw: RawMessage, verdict: GuardVerdict, *, seal: object) -> None:
        if seal is not _seal.SEAL:
            raise PermissionError("CheckedMessage can only be created by the input guardrail")
        if not verdict.allowed:
            raise ValueError("cannot mint a CheckedMessage from a blocking verdict")
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_verdict", verdict)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("CheckedMessage is immutable")

    @property
    def raw(self) -> RawMessage:
        return self._raw

    @property
    def verdict(self) -> GuardVerdict:
        return self._verdict

    @property
    def text(self) -> str:
        return self._raw.text

    @property
    def session_id(self) -> str:
        return self._raw.session_id

    def __repr__(self) -> str:
        return f"CheckedMessage(session_id={self.session_id!r}, text={self.text!r})"


class BlockedMessage(BaseModel):
    """Input the guardrail refused. Never reaches the router or any handler."""

    model_config = ConfigDict(frozen=True)

    raw: RawMessage
    verdict: GuardVerdict
    safe_reply: str


class RouteDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    order_id: str | None = None
    cached: bool = False
    rationale: str = ""


class Passage(BaseModel):
    model_config = ConfigDict(frozen=True)

    doc_id: str
    chunk_id: str
    title: str
    text: str
    score: float


class ToolResult(BaseModel):
    """Output of a tool call. `confirmed_fields` lists the keys of `data` the system of record
    actually confirmed; only those may be stated to the customer as facts (amounts, dates,
    verdicts).
    """

    model_config = ConfigDict(frozen=True)

    tool: str
    args: dict[str, Any]
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    confirmed_fields: tuple[str, ...] = ()
    error: str | None = None


class DraftReply(BaseModel):
    """A handler's proposed answer plus the evidence it was built from."""

    text: str
    intent: Intent
    handler: str
    passages: tuple[Passage, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    needs_human: bool = False


class FinalReply(BaseModel):
    session_id: str
    text: str
    intent: Intent | None = None
    handler: str | None = None
    blocked: bool = False  # input guard refused the message
    rewritten: bool = False  # output guard changed the draft
    guard_notes: tuple[str, ...] = ()
    trace_id: str | None = None


# ---- Interfaces -------------------------------------------------------------------------------


@runtime_checkable
class InputGuard(Protocol):
    async def check(self, raw: RawMessage) -> CheckedMessage | BlockedMessage: ...


@runtime_checkable
class OutputGuard(Protocol):
    async def check(self, draft: DraftReply, msg: CheckedMessage) -> FinalReply: ...


@runtime_checkable
class Router(Protocol):
    async def route(self, msg: CheckedMessage) -> RouteDecision: ...


@runtime_checkable
class Retriever(Protocol):
    async def retrieve(self, query: str, k: int = 4) -> list[Passage]: ...


class Handler(ABC):
    """Base class for intent handlers (agents). Register one per intent in the harness registry."""

    name: ClassVar[str]
    intents: ClassVar[tuple[Intent, ...]]
    description: ClassVar[str]  # used to build the router prompt from the registry
    # Optional per-intent router text for handlers serving several intents (else `description`)
    intent_descriptions: ClassVar[Mapping[Intent, str]] = {}

    async def __call__(self, msg: CheckedMessage, route: RouteDecision) -> DraftReply:
        if not isinstance(msg, CheckedMessage):
            raise TypeError(f"{self.name} only accepts CheckedMessage, got {type(msg).__name__}")
        return await self.handle(msg, route)

    @abstractmethod
    async def handle(self, msg: CheckedMessage, route: RouteDecision) -> DraftReply: ...
