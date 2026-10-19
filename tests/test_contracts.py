import pytest

from support_assistant import _seal
from support_assistant.contracts import (
    CheckedMessage,
    DraftReply,
    GuardVerdict,
    Handler,
    Intent,
    RawMessage,
    RouteDecision,
)

RAW = RawMessage(session_id="s1", text="hello")
OK = GuardVerdict(allowed=True, checker="test")


def test_checked_message_requires_seal():
    with pytest.raises(PermissionError):
        CheckedMessage(RAW, OK, seal=object())


def test_checked_message_rejects_blocking_verdict():
    with pytest.raises(ValueError):
        CheckedMessage(RAW, GuardVerdict(allowed=False, checker="test"), seal=_seal.SEAL)


def test_checked_message_is_immutable():
    msg = CheckedMessage(RAW, OK, seal=_seal.SEAL)
    with pytest.raises(AttributeError):
        msg._raw = RawMessage(session_id="s1", text="injected")  # type: ignore[misc]


class _Echo(Handler):
    name = "echo"
    intents = (Intent.PRODUCT_QUESTION,)
    description = "test"

    async def handle(self, msg, route):
        return DraftReply(text=msg.text, intent=route.intent, handler=self.name)


async def test_handler_rejects_raw_message():
    route = RouteDecision(intent=Intent.PRODUCT_QUESTION, confidence=1.0)
    with pytest.raises(TypeError):
        await _Echo()(RAW, route)  # type: ignore[arg-type]
    ok = await _Echo()(CheckedMessage(RAW, OK, seal=_seal.SEAL), route)
    assert ok.text == "hello"
