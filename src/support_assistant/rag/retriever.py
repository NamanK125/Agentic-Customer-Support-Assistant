"""Dense retriever with a minimum-similarity gate, plus the `build_retriever` factory."""

from __future__ import annotations

from pathlib import Path

from support_assistant.config import Settings, get_settings
from support_assistant.contracts import Passage
from support_assistant.observability import meter, tracer
from support_assistant.rag.embedder import Embedder, SentenceTransformerEmbedder
from support_assistant.rag.index import NumpyIndex, VectorIndex, load_or_build_index

_tracer = tracer(__name__)
_meter = meter(__name__)
_top_score = _meter.create_histogram("rag.top_score")
_empty = _meter.create_counter("rag.retrieve.below_threshold")

REPO_ROOT = Path(__file__).resolve().parents[3]

# Cosine thresholds are model specific (score distributions differ). Calibrated on
# eval/data/retrieval_eval.jsonl, see eval/results/retrieval_*.md. Override: RAG_MIN_SCORE.
DEFAULT_MIN_SCORE: dict[str, float] = {
    "BAAI/bge-small-en-v1.5": 0.65,
    "sentence-transformers/all-MiniLM-L6-v2": 0.35,
}
FALLBACK_MIN_SCORE = 0.30


def min_score_for(model_id: str, override: float | None = None) -> float:
    if override is not None:
        return override
    return DEFAULT_MIN_SCORE.get(model_id, FALLBACK_MIN_SCORE)


class DenseRetriever:
    """Implements `contracts.Retriever`. Returns only passages with score >= `min_score`."""

    def __init__(self, embedder: Embedder, index: VectorIndex, *, min_score: float) -> None:
        self.embedder = embedder
        self.index = index
        self.min_score = min_score

    async def retrieve(self, query: str, k: int = 4) -> list[Passage]:
        with _tracer.start_as_current_span("rag.retrieve") as span:
            qvec = await self.embedder.aembed_query(query)
            hits = self.index.search(qvec, k)
            top = hits[0][1] if hits else 0.0
            span.set_attribute("rag.k", k)
            span.set_attribute("rag.top_score", top)
            span.set_attribute("rag.min_score", self.min_score)
            _top_score.record(top)
            kept = [(c, s) for c, s in hits if s >= self.min_score]
            span.set_attribute("rag.returned", len(kept))
            if not kept:
                _empty.add(1)
            return [
                Passage(doc_id=c.doc_id, chunk_id=c.chunk_id, title=c.title, text=c.text, score=s)
                for c, s in kept
            ]


def build_retriever(settings: Settings | None = None) -> DenseRetriever:
    """Load the persisted index (rebuilding if the KB or model changed) and wrap it.

    Synchronous: call at startup (it may embed the KB once, a few seconds on CPU).
    """
    s = settings or get_settings()
    kb_dir = Path(s.kb_dir) if s.kb_dir else REPO_ROOT / "kb"
    cache_dir = Path(s.rag_cache_dir) if s.rag_cache_dir else REPO_ROOT / ".cache" / "rag"
    embedder = SentenceTransformerEmbedder(s.embedding_model)
    index, _ = load_or_build_index(kb_dir, embedder, cache_dir)
    assert isinstance(index, NumpyIndex)
    # Warm up: with a cached index the model would otherwise load lazily on the first customer
    # query (~14 s on CPU), which blew the first live turn's handler budget.
    embedder.embed_query("warm up")
    return DenseRetriever(
        embedder, index, min_score=min_score_for(s.embedding_model, s.rag_min_score)
    )
