"""Build or rebuild the RAG index and print stats (--force ignores the cache)."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from support_assistant.config import get_settings
from support_assistant.rag.embedder import SentenceTransformerEmbedder
from support_assistant.rag.index import load_or_build_index
from support_assistant.rag.retriever import REPO_ROOT, min_score_for


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="rebuild even if the cache is current")
    args = ap.parse_args()

    s = get_settings()
    model = s.embedding_model
    kb_dir = Path(s.kb_dir) if s.kb_dir else REPO_ROOT / "knowledge_base"
    cache_dir = Path(s.rag_cache_dir) if s.rag_cache_dir else REPO_ROOT / ".cache" / "rag"
    start = time.perf_counter()
    index, rebuilt = load_or_build_index(
        kb_dir, SentenceTransformerEmbedder(model), cache_dir, force=args.force
    )
    elapsed = time.perf_counter() - start
    docs = {c.doc_id for c in index.chunks}
    words = [len(c.text.split()) for c in index.chunks]
    print(f"model       : {model}")
    print(f"kb dir      : {kb_dir}")
    print(f"cache dir   : {cache_dir}")
    print(f"status      : {'rebuilt' if rebuilt else 'up to date (loaded from cache)'}")
    print(f"documents   : {len(docs)}")
    print(f"chunks      : {len(index)} (words/chunk min {min(words)}, max {max(words)})")
    print(f"vector dim  : {index.vectors.shape[1]}")
    print(f"min score   : {min_score_for(model, s.rag_min_score)}")
    print(f"elapsed     : {elapsed:.1f}s")


if __name__ == "__main__":
    main()
