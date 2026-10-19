from types import SimpleNamespace

import httpx
import openai
import pytest

from support_assistant.config import Settings
from support_assistant.llm.client import CircuitBreaker, LLMClient, LLMError


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_breaker_opens_after_threshold_and_probes_after_cooldown():
    clock = _Clock()
    b = CircuitBreaker(3, 10.0, clock=clock)
    for _ in range(2):
        b.record(ok=False)
    assert b.allow()  # 2 failures < threshold
    b.record(ok=False)
    assert not b.allow()  # open
    clock.t = 10.0
    assert b.allow()  # half-open: one probe
    assert not b.allow()  # only one probe at a time
    b.record(ok=False)  # failed probe restarts the cooldown
    clock.t = 15.0
    assert not b.allow()
    clock.t = 20.0
    assert b.allow()
    b.record(ok=True)
    assert b.allow() and not b.is_open


def _resp(content: str) -> SimpleNamespace:
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None, model="m")


def _bad_request() -> openai.BadRequestError:
    req = httpx.Request("POST", "http://x/v1/chat/completions")
    return openai.BadRequestError(
        "tool choice requires --enable-auto-tool-choice",
        response=httpx.Response(400, request=req),
        body=None,
    )


def _client(create, **settings) -> LLMClient:
    c = LLMClient(
        Settings(_env_file=None, llm_max_retries=0, llm_circuit_failures=2, **settings)
    )
    c._client = lambda: SimpleNamespace(  # type: ignore[method-assign]
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return c


async def test_think_block_is_stripped_and_thinking_disabled():
    seen = {}

    async def create(**kw):
        seen.update(kw)
        return _resp("We need to say OK.\n</think>\n\nOK")

    client = _client(create, llm_disable_thinking=True)
    out = await client.chat([{"role": "user", "content": "hi"}])
    assert out.content == "OK"
    assert seen["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


async def test_client_errors_do_not_trip_the_breaker():
    async def create(**kw):
        raise _bad_request()

    c = _client(create)
    for _ in range(5):
        with pytest.raises(LLMError):
            await c.chat([{"role": "user", "content": "hi"}])
    assert not c._breaker.is_open


async def test_outage_opens_breaker_then_fails_fast():
    calls = 0

    async def create(**kw):
        nonlocal calls
        calls += 1
        raise openai.APIConnectionError(request=httpx.Request("POST", "http://x"))

    c = _client(create)
    for _ in range(4):
        with pytest.raises(LLMError):
            await c.chat([{"role": "user", "content": "hi"}])
    assert c._breaker.is_open
    assert calls == 2  # calls 3 and 4 never reached the network
