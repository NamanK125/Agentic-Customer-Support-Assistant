"""Provider, fallback and fault-injection tests. Offline by default: the in-house protocol is
served by a local fake WebSocket server. Live tests need RUN_VOICE_LIVE=1."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import websockets

from support_assistant.config import Settings
from support_assistant.voice.canned import PHRASES, CannedAudio
from support_assistant.voice.contracts import CallEvent, STTError, Transcript, TTSError
from support_assistant.voice.faults import FaultInjector, FaultySTT, FaultyTTS
from support_assistant.voice.providers import build_stt, build_tts
from support_assistant.voice.resilience import FallbackSTT, FallbackTTS
from support_assistant.voice.stt import GroqWhisperSTT
from support_assistant.voice.tts import InHouseStreamTTS

PCM = b"\x01\x00" * 1600


# ---- fake in-house server ---------------------------------------------------------------------


class FakeServer:
    def __init__(self) -> None:
        self.mode = "ok"  # ok | error | stall | drop
        self.chunks = 3
        self.closed = asyncio.Event()
        self.received: list[dict[str, Any]] = []
        self.url = ""

    async def handler(self, ws: Any) -> None:
        try:
            while True:
                msg = json.loads(await ws.recv())
                self.received.append(msg)
                if msg["type"] == "flush":
                    break
            await ws.send(json.dumps({"type": "metadata"}))
            if self.mode == "error":
                await ws.send(json.dumps({"type": "error", "msg": "bad speaker"}))
                return
            if self.mode == "stall":
                await ws.wait_closed()
            for i in range(self.chunks):
                if self.mode == "drop" and i == 1:
                    await ws.close(code=1011)
                    return
                await ws.send(json.dumps({"type": "chunk", "chunk_number": i}))
                await ws.send(PCM)
                await asyncio.sleep(0.02 if self.mode != "slow" else 0.3)
            await ws.send(json.dumps({"type": "done", "total_chunks": self.chunks}))
            self.received.append(json.loads(await ws.recv()))
        except websockets.ConnectionClosed:
            pass
        finally:
            self.closed.set()


@pytest.fixture
async def server():
    srv = FakeServer()
    async with websockets.serve(srv.handler, "127.0.0.1", 0) as s:
        port = s.sockets[0].getsockname()[1]
        srv.url = f"ws://127.0.0.1:{port}/v1/tts/stream"
        yield srv


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)


async def collect(it: AsyncIterator[bytes]) -> list[bytes]:
    return [c async for c in it]


class Rec:
    def __init__(self) -> None:
        self.events: list[CallEvent] = []

    def __call__(self, e: CallEvent) -> None:
        self.events.append(e)

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]


# ---- fakes for the chains ---------------------------------------------------------------------


class FakeSTT:
    def __init__(self, name: str, *outcomes: Any) -> None:
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = 0

    async def transcribe(self, pcm: bytes) -> Transcript:
        self.calls += 1
        o = self.outcomes[min(self.calls, len(self.outcomes)) - 1]
        if o == "hang":
            await asyncio.Event().wait()
        if isinstance(o, Exception):
            raise o
        return Transcript(text=o, provider=self.name, latency_ms=1)


class FakeTTS:
    def __init__(
        self,
        name: str,
        chunks: list[bytes],
        fail_after: int | None = None,
        retryable: bool = True,
        hang: bool = False,
    ) -> None:
        self.name, self.chunks, self.fail_after = name, chunks, fail_after
        self.retryable, self.hang = retryable, hang
        self.calls = 0

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        self.calls += 1
        if self.hang:
            await asyncio.Event().wait()
        for i, c in enumerate(self.chunks):
            if self.fail_after is not None and i == self.fail_after:
                raise TTSError(self.name, "boom", retryable=self.retryable)
            yield c


# ---- InHouseStreamTTS against the fake server -------------------------------------------------


async def test_inhouse_happy_path(server: FakeServer) -> None:
    tts = InHouseStreamTTS(settings(), url=server.url)
    out = await collect(tts.stream("hello"))
    assert out == [PCM] * 3
    await asyncio.wait_for(server.closed.wait(), 2)
    assert [m["type"] for m in server.received] == ["config", "text", "flush", "client_complete"]
    assert server.received[0]["audio_codec"] == "pcm_16000"
    assert server.received[1]["text_input"] == "hello"


async def test_inhouse_error_frame(server: FakeServer) -> None:
    server.mode = "error"
    with pytest.raises(TTSError, match="bad speaker"):
        await collect(InHouseStreamTTS(settings(), url=server.url).stream("x"))


async def test_inhouse_connection_refused_is_retryable() -> None:
    from support_assistant.voice.faults import unreachable_inhouse

    with pytest.raises(TTSError) as ei:
        await collect(unreachable_inhouse(settings()).stream("x"))
    assert ei.value.retryable


async def test_inhouse_drop_mid_stream(server: FakeServer) -> None:
    server.mode = "drop"
    got: list[bytes] = []
    with pytest.raises(TTSError) as ei:
        async for c in InHouseStreamTTS(settings(), url=server.url).stream("x"):
            got.append(c)
    assert len(got) == 1 and ei.value.retryable


async def test_inhouse_cancellation_closes_socket(server: FakeServer) -> None:
    server.mode = "slow"
    server.chunks = 50
    tts = InHouseStreamTTS(settings(), url=server.url)
    first = asyncio.Event()

    async def consume() -> None:
        async for _ in tts.stream("x"):
            first.set()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(first.wait(), 3)
    t0 = time.perf_counter()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(server.closed.wait(), 1.0)  # server saw the socket go away
    assert time.perf_counter() - t0 < 1.0


async def test_inhouse_aclose_closes_socket(server: FakeServer) -> None:
    server.mode = "slow"
    server.chunks = 50
    it = InHouseStreamTTS(settings(), url=server.url).stream("x")
    await it.__anext__()
    await it.aclose()
    await asyncio.wait_for(server.closed.wait(), 1.0)


# ---- FallbackTTS ------------------------------------------------------------------------------


def tts_chain(*providers: Any, rec: Rec | None = None, first: float = 0.3, chunk: float = 0.3):
    return FallbackTTS(
        providers,
        first_chunk_timeout_s=first,
        chunk_timeout_s=chunk,
        events=rec,
        circuit_failures=2,
        circuit_cooldown_s=60,
    )


async def test_tts_stall_times_out_and_falls_back(server: FakeServer) -> None:
    server.mode = "stall"
    rec = Rec()
    real = InHouseStreamTTS(settings(), url=server.url)
    backup = FakeTTS("edge", [b"ab", b"cd"])
    t0 = time.perf_counter()
    out = await collect(tts_chain(real, backup, rec=rec).stream("hi"))
    assert out == [b"ab", b"cd"]
    assert time.perf_counter() - t0 < 1.5
    assert "provider_fallback" in rec.kinds()


async def test_tts_mid_stream_drop_restarts_on_next_provider(server: FakeServer) -> None:
    server.mode = "drop"
    rec = Rec()
    backup = FakeTTS("edge", [b"ab", b"cd"])
    out = await collect(
        tts_chain(InHouseStreamTTS(settings(), url=server.url), backup, rec=rec).stream("hi")
    )
    assert out == [PCM, b"ab", b"cd"]  # first chunk heard twice-ish: restart from the start
    fb = next(e for e in rec.events if e.kind == "provider_fallback")
    assert fb.data["restart"] is True and fb.data["chunks_already_sent"] == 1


async def test_tts_ordering_and_all_fail_raises() -> None:
    a = FakeTTS("a", [b"1"], fail_after=0)
    b = FakeTTS("b", [b"1"], fail_after=0, retryable=False)
    with pytest.raises(TTSError, match="all TTS providers failed"):
        await collect(tts_chain(a, b).stream("x"))
    assert (a.calls, b.calls) == (1, 1)


async def test_tts_hang_bounded_by_first_chunk_timeout() -> None:
    chain = tts_chain(FakeTTS("a", [], hang=True), FakeTTS("b", [b"ok"]), first=0.2)
    t0 = time.perf_counter()
    assert await collect(chain.stream("x")) == [b"ok"]
    assert 0.2 <= time.perf_counter() - t0 < 1.0


async def test_tts_breaker_skips_dead_provider() -> None:
    rec = Rec()
    a = FakeTTS("a", [], hang=True)
    chain = tts_chain(a, FakeTTS("b", [b"ok"]), rec=rec, first=0.1)
    for _ in range(2):
        await collect(chain.stream("x"))
    assert a.calls == 2
    t0 = time.perf_counter()
    await collect(chain.stream("x"))
    assert a.calls == 2 and time.perf_counter() - t0 < 0.05
    assert "provider_skipped_open_circuit" in rec.kinds()


async def test_tts_consumer_close_is_not_a_failure() -> None:
    a = FakeTTS("a", [b"1", b"2", b"3"])
    chain = tts_chain(a, FakeTTS("b", [b"x"]))
    it = chain.stream("x")
    await it.__anext__()
    await it.aclose()
    assert chain.breaker("a")._failures == 0


# ---- FallbackSTT ------------------------------------------------------------------------------


def stt_chain(*providers: Any, rec: Rec | None = None, timeout: float = 0.3):
    return FallbackSTT(
        providers,
        timeout_s=timeout,
        backoff_s=0.01,
        events=rec,
        circuit_failures=2,
        circuit_cooldown_s=60,
    )


async def test_stt_retries_retryable_once_then_ok() -> None:
    a = FakeSTT("a", STTError("a", "5xx", retryable=True), "hello")
    t = await stt_chain(a, FakeSTT("b", "no")).transcribe(PCM)
    assert t.text == "hello" and a.calls == 2
    assert t.attempts == ("a:retryable_error", "a:ok")


async def test_stt_non_retryable_goes_straight_to_next() -> None:
    a = FakeSTT("a", STTError("a", "401", retryable=False))
    t = await stt_chain(a, FakeSTT("b", "from b")).transcribe(PCM)
    assert a.calls == 1 and t.provider == "b" and t.attempts == ("a:error", "b:ok")


async def test_stt_hang_bounded_by_timeout() -> None:
    rec = Rec()
    t0 = time.perf_counter()
    t = await stt_chain(FakeSTT("a", "hang"), FakeSTT("b", "ok"), rec=rec).transcribe(PCM)
    elapsed = time.perf_counter() - t0
    assert t.text == "ok" and elapsed < 0.3 * 2 + 0.5  # two timed-out attempts at most
    assert {"provider_attempt", "provider_error", "provider_fallback", "provider_ok"} <= set(
        rec.kinds()
    )


async def test_stt_breaker_skip_and_all_fail() -> None:
    rec = Rec()
    a = FakeSTT("a", STTError("a", "401", retryable=False))
    chain = stt_chain(a, FakeSTT("b", "ok"), rec=rec)
    for _ in range(2):
        await chain.transcribe(PCM)
    t = await chain.transcribe(PCM)
    assert a.calls == 2 and t.attempts[0] == "a:circuit_open"
    assert "provider_skipped_open_circuit" in rec.kinds()
    dead = stt_chain(FakeSTT("x", STTError("x", "no", retryable=False)))
    with pytest.raises(STTError):
        await dead.transcribe(PCM)


async def test_groq_without_key_fails_fast_non_retryable() -> None:
    stt = GroqWhisperSTT(settings(llm_api_key="EMPTY", stt_api_key=""))
    with pytest.raises(STTError) as ei:
        await stt.transcribe(PCM)
    assert not ei.value.retryable


# ---- FaultInjector ----------------------------------------------------------------------------


async def test_fault_refuse_and_invalid_key() -> None:
    inj = FaultInjector()
    stt = FaultySTT(FakeSTT("groq-whisper", "ok"), inj)
    assert (await stt.transcribe(PCM)).text == "ok"
    inj.set("stt", "refuse")
    with pytest.raises(STTError) as ei:
        await stt.transcribe(PCM)
    assert ei.value.retryable
    inj.set("groq-whisper", "invalid_key")  # provider-specific wins over kind
    with pytest.raises(STTError) as ei:
        await stt.transcribe(PCM)
    assert not ei.value.retryable
    inj.clear("groq-whisper")
    inj.clear("stt")
    assert (await stt.transcribe(PCM)).text == "ok"


async def test_fault_hang_caught_by_chain_not_fault() -> None:
    inj = FaultInjector()
    inj.set("tts", "hang")
    chain = tts_chain(
        FaultyTTS(FakeTTS("inhouse", [b"1"]), inj), FakeTTS("edge", [b"e"]), first=0.2
    )
    assert await collect(chain.stream("x")) == [b"e"]


async def test_fault_drop_after_chunks_and_slow() -> None:
    inj = FaultInjector()
    tts = FaultyTTS(FakeTTS("inhouse", [b"1", b"2", b"3"]), inj)
    inj.set("inhouse", "drop_after_chunks:2")
    got: list[bytes] = []
    with pytest.raises(TTSError):
        async for c in tts.stream("x"):
            got.append(c)
    assert got == [b"1", b"2"]
    inj.set("inhouse", "slow:150")
    t0 = time.perf_counter()
    assert len(await collect(tts.stream("x"))) == 3
    assert time.perf_counter() - t0 >= 0.15
    with pytest.raises(ValueError):
        inj.set("inhouse", "explode")


async def test_fault_unreachable_is_real_network_failure() -> None:
    inj = FaultInjector()
    inj.set("inhouse", "unreachable")
    tts = FaultyTTS(InHouseStreamTTS(settings(), url="ws://127.0.0.1:1/x"), inj)
    with pytest.raises(TTSError, match="cannot connect"):
        await collect(tts.stream("x"))


async def test_build_chains_orders_and_events() -> None:
    rec = Rec()
    inj = FaultInjector(rec)
    s = settings()
    assert [p.name for p in build_stt(s, inj, rec).providers] == ["groq-whisper", "local-whisper"]
    assert [p.name for p in build_tts(s, inj, rec).providers] == ["inhouse", "edge"]
    inj.set("tts", "refuse")
    assert rec.kinds() == ["fault_injected"]


# ---- canned audio -----------------------------------------------------------------------------


async def test_canned_renders_caches_and_falls_back_to_chime(tmp_path: Any) -> None:
    c = CannedAudio(settings(), tmp_path)
    await c.prepare(FakeTTS("inhouse", [b"\x01\x00" * 100]))
    assert c.get("one_moment") == b"\x01\x00" * 100
    assert len(list(tmp_path.glob("*.pcm"))) == len(PHRASES)
    c2 = CannedAudio(settings(), tmp_path)
    await c2.prepare(FakeTTS("inhouse", [], fail_after=0))  # cache hit, TTS not needed
    assert c2.get("trouble") == b"\x01\x00" * 100
    c3 = CannedAudio(settings(), tmp_path / "empty")
    await c3.prepare(FakeTTS("inhouse", [b"x"], fail_after=0))
    assert len(c3.get("didnt_catch")) > 1000 and set(c3.rendered_with.values()) == {"chime"}


# ---- live (RUN_VOICE_LIVE=1) ------------------------------------------------------------------

live = pytest.mark.skipif(os.environ.get("RUN_VOICE_LIVE") != "1", reason="RUN_VOICE_LIVE!=1")


@live
async def test_live_inhouse_and_local_whisper() -> None:
    from support_assistant.config import get_settings
    from support_assistant.voice.stt import LocalWhisperSTT

    s = get_settings()
    pcm = b"".join(await collect(InHouseStreamTTS(s).stream("Where is my order?")))
    assert len(pcm) > 16000
    t = await LocalWhisperSTT(s).transcribe(pcm)
    assert "order" in t.text.lower()
