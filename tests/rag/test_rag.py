"""Offline RAG tests: HashingEmbedder + FakeLLM. Real-model tests need RUN_EMBEDDING_TESTS=1."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from support_assistant.contracts import Intent, Passage, RouteDecision
from support_assistant.llm.client import ChatResult
from support_assistant.llm.fake import FakeLLM
from support_assistant.rag import RagAgent
from support_assistant.rag.agent import NO_ANSWER_TEXT, extractive_answer, verify_citations
from support_assistant.rag.chunker import chunk_document
from support_assistant.rag.embedder import HashingEmbedder, SentenceTransformerEmbedder
from support_assistant.rag.index import kb_hash, load_or_build_index
from support_assistant.rag.retriever import DenseRetriever

KB_DIR = Path(__file__).resolve().parents[2] / "kb"
ROUTE = RouteDecision(intent=Intent.PRODUCT_QUESTION, confidence=0.9)

DOC_A = """---
id: ship
title: Shipping
tags: [shipping]
---
# Shipping

## Standard
Standard shipping is free on orders of 999 rupees or more and takes 3 to 6 business days.

## Express
Express shipping costs 149 rupees and takes 1 to 2 business days in metro cities.
"""
DOC_B = """---
id: bp
title: BP monitor errors
tags: [bp]
---
## E2
Error E2 means you moved or talked during the blood pressure measurement. Sit still.
"""


def msg(text: str) -> Any:
    return SimpleNamespace(text=text)  # handle() only reads .text


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    d = tmp_path / "kb"
    d.mkdir()
    (d / "ship.md").write_text(DOC_A)
    (d / "bp.md").write_text(DOC_B)
    return d


def make_retriever(kb: Path, tmp_path: Path, min_score: float = 0.15) -> DenseRetriever:
    emb = HashingEmbedder()
    index, _ = load_or_build_index(kb, emb, tmp_path / "cache")
    return DenseRetriever(emb, index, min_score=min_score)


# ---- chunking ----------------------------------------------------------------------------


def test_chunk_carries_doc_metadata_and_headings() -> None:
    chunks = chunk_document(DOC_A, fallback_id="x")
    assert chunks[0].doc_id == "ship" and chunks[0].title == "Shipping"
    assert chunks[0].chunk_id == "ship#0"
    assert "Standard" in chunks[0].heading
    assert chunks[0].text.startswith("## Standard")


def test_long_section_splits_with_overlap() -> None:
    words = " ".join(f"w{i}" for i in range(500))
    chunks = chunk_document(f"---\nid: d\ntitle: T\n---\n## S\n{words}", fallback_id="d")
    assert len(chunks) >= 3
    assert all(len(c.text.split()) <= 225 for c in chunks)
    first, second = chunks[0].text.split(), chunks[1].text.split()
    assert set(first[-25:]) & set(second[:30])  # overlap


def test_real_kb_chunks_are_reasonable() -> None:
    from support_assistant.rag.chunker import load_kb

    chunks = load_kb(KB_DIR)
    assert len({c.doc_id for c in chunks}) >= 12
    assert all(len(c.text.split()) <= 250 for c in chunks)


# ---- index -------------------------------------------------------------------------------


def test_index_persists_and_reloads_without_rebuild(kb: Path, tmp_path: Path) -> None:
    emb = HashingEmbedder()
    _, rebuilt = load_or_build_index(kb, emb, tmp_path / "c")
    assert rebuilt
    idx, rebuilt = load_or_build_index(kb, emb, tmp_path / "c")
    assert not rebuilt and len(idx) >= 2


def test_index_rebuilds_when_kb_or_model_changes(kb: Path, tmp_path: Path) -> None:
    cache = tmp_path / "c"
    load_or_build_index(kb, HashingEmbedder(), cache)
    before = kb_hash(kb)
    (kb / "bp.md").write_text(DOC_B + "\nMore text.")
    assert kb_hash(kb) != before
    _, rebuilt = load_or_build_index(kb, HashingEmbedder(), cache)
    assert rebuilt
    _, rebuilt = load_or_build_index(kb, HashingEmbedder(dim=256), cache)  # different model id
    assert rebuilt


# ---- retriever ---------------------------------------------------------------------------


async def test_retriever_returns_scored_passages(kb: Path, tmp_path: Path) -> None:
    r = make_retriever(kb, tmp_path)
    hits = await r.retrieve("what does error E2 mean on the blood pressure monitor", 3)
    assert hits[0].doc_id == "bp"
    assert hits == sorted(hits, key=lambda p: -p.score)


async def test_retriever_threshold_filters(kb: Path, tmp_path: Path) -> None:
    r = make_retriever(kb, tmp_path, min_score=0.95)
    assert await r.retrieve("express shipping cost", 3) == []


# ---- agent -------------------------------------------------------------------------------


def llm_saying(text: str) -> FakeLLM:
    return FakeLLM(lambda _m, _k: ChatResult(content=text))


async def test_below_threshold_no_answer_and_no_llm_call(kb: Path, tmp_path: Path) -> None:
    llm = llm_saying("should never be used [1]")
    agent = RagAgent(llm, make_retriever(kb, tmp_path, min_score=0.95))
    draft = await agent.handle(msg("express shipping cost"), ROUTE)
    assert llm.calls == []
    assert draft.text == NO_ANSWER_TEXT and draft.needs_human
    assert draft.passages == ()


async def test_grounded_answer_attaches_passages(kb: Path, tmp_path: Path) -> None:
    llm = llm_saying("Express shipping costs 149 rupees [1].")
    agent = RagAgent(llm, make_retriever(kb, tmp_path))
    draft = await agent.handle(msg("how much is express shipping"), ROUTE)
    assert draft.text == "Express shipping costs 149 rupees [1]."
    assert not draft.needs_human and draft.handler == "rag_agent"
    assert draft.passages and draft.passages[0].doc_id == "ship"
    sent = llm.calls[0]["messages"]
    assert '<passage id="1"' in sent[1]["content"]
    assert "ONLY" in sent[0]["content"]


async def test_invalid_citations_stripped(kb: Path, tmp_path: Path) -> None:
    agent = RagAgent(
        llm_saying("Express costs 149 rupees [1] and is great [9]."), make_retriever(kb, tmp_path)
    )
    draft = await agent.handle(msg("how much is express shipping"), ROUTE)
    assert "[9]" not in draft.text and "[1]" in draft.text


async def test_answer_without_valid_citation_falls_back_to_no_answer(
    kb: Path, tmp_path: Path
) -> None:
    for bad in ("Express costs 149 rupees.", "Express costs 149 rupees [7].", "I don't know."):
        agent = RagAgent(llm_saying(bad), make_retriever(kb, tmp_path))
        draft = await agent.handle(msg("how much is express shipping"), ROUTE)
        assert draft.text == NO_ANSWER_TEXT and draft.needs_human


async def test_llm_outage_returns_attributed_extractive_answer(kb: Path, tmp_path: Path) -> None:
    agent = RagAgent(FakeLLM(fail=True), make_retriever(kb, tmp_path))
    draft = await agent.handle(msg("how much is express shipping"), ROUTE)
    assert 'help article "Shipping"' in draft.text
    assert "149" in draft.text and "[1]" in draft.text
    assert len(draft.passages) == 1 and not draft.needs_human


def test_verify_citations_handles_groups() -> None:
    text, valid = verify_citations("A [1, 5] B [3].", 2)
    assert text == "A [1] B." and valid == [1]


async def test_query_embedding_runs_off_the_event_loop() -> None:
    import threading

    seen: list[int] = []

    class Spy(HashingEmbedder):
        def embed_query(self, text: str):  # type: ignore[no-untyped-def]
            seen.append(threading.get_ident())
            return super().embed_query(text)

    await Spy().aembed_query("hi")
    assert seen and seen[0] != threading.get_ident()


# ---- real models (opt-in) ----------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("RUN_EMBEDDING_TESTS") != "1", reason="set RUN_EMBEDDING_TESTS=1"
)
async def test_real_model_retrieves_expected_docs(tmp_path: Path) -> None:
    emb = SentenceTransformerEmbedder("sentence-transformers/all-MiniLM-L6-v2")
    index, _ = load_or_build_index(KB_DIR, emb, tmp_path / "c")
    r = DenseRetriever(emb, index, min_score=0.35)
    hits = await r.retrieve("How much is express delivery and which cities?", 3)
    assert hits and hits[0].doc_id == "shipping"
    assert await r.retrieve("who won the cricket world cup", 3) == []


def test_extractive_fallback_picks_the_section_matching_the_specific_term():
    # Regression: plain word overlap picked the E5 section ("The BP-200 could not pair...")
    # for an E2 question, because "the"/"BP-200" outscored the one word that mattered.
    text = (
        "BP-200 Error Codes\n"
        "## E2: movement or talking detected\n"
        "You moved or talked during the measurement. Stay still and quiet, then try again.\n"
        "## E5: Bluetooth pairing failed\n"
        "The BP-200 could not pair with the app. Retry pairing from the app."
    )
    p = Passage(doc_id="d", chunk_id="d#0", title="BP-200 Error Codes", text=text, score=0.9)
    answer, quoted = extractive_answer("What do the E2 error code on the BP-200 mean?", [p])
    assert "movement or talking" in answer
    assert "pair" not in answer
    assert answer.endswith("[1]") and quoted is p


def test_extractive_fallback_searches_all_passages_and_declines_without_a_match():
    # Regression: the top-ranked chunk held E3-E5 only; the E2 section was in the second one.
    def p(i: int, body: str) -> Passage:
        return Passage(doc_id="d", chunk_id=f"d#{i}", title="BP-200 Error Codes E1-E5",
                       text=body, score=0.9 - i / 10)  # fmt: skip

    later = p(0, "## E3: cuff overinflated\nThe cuff inflated too far. Release it and retry.")
    e2 = p(1, "## E2: movement or talking detected\nYou moved or talked. Stay still, retry.")
    answer, quoted = extractive_answer("what does E2 mean", [later, e2])
    assert "movement or talking" in answer and quoted is e2
    assert extractive_answer("do you sell glucose meters", [later, e2]) is None
