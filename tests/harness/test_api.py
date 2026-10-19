"""HTTP API with an injected fake pipeline (no LLM, no guardrail/RAG packages needed)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from support_assistant.api import create_app
from support_assistant.contracts import FinalReply, Intent
from support_assistant.harness import CacheStats


class FakePipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def handle(self, session_id: str, text: str, channel: str = "text") -> FinalReply:
        self.calls.append((session_id, text, channel))
        return FinalReply(
            session_id=session_id, text=f"echo {text}", intent=Intent.ORDER_STATUS, trace_id="t"
        )

    async def cache_stats(self) -> CacheStats | None:
        return CacheStats(backend="memory", hits=3, misses=1)


def test_chat_health_and_stats():
    fake = FakePipeline()
    with TestClient(create_app(pipeline=fake)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        r = client.post("/v1/chat", json={"session_id": "abc-1", "text": "where is PN-10001"})
        assert r.status_code == 200
        assert r.json()["text"] == "echo where is PN-10001" and r.json()["intent"] == "order_status"
        assert fake.calls == [("abc-1", "where is PN-10001", "text")]
        stats = client.get("/v1/cache/stats").json()
        assert stats == {"backend": "memory", "hits": 3, "misses": 1, "hit_rate": 0.75}


def test_request_validation():
    with TestClient(create_app(pipeline=FakePipeline())) as client:
        assert (
            client.post("/v1/chat", json={"session_id": "a b/../", "text": "x"}).status_code == 422
        )
        assert client.post("/v1/chat", json={"session_id": "a", "text": ""}).status_code == 422
        assert (
            client.post("/v1/chat", json={"session_id": "a", "text": "x" * 4001}).status_code == 422
        )
