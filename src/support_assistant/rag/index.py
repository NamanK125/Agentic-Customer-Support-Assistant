"""Vector index: a protocol, an in-process numpy implementation, and on-disk persistence.

Scaling note: `NumpyIndex` does an exact brute-force cosine scan, which is instant for the few
hundred chunks of a support KB. For millions of chunks, implement `VectorIndex` on top of
pgvector (``ORDER BY embedding <=> $1 LIMIT k`` with an HNSW index) or Qdrant (``search`` on a
cosine collection) and swap it in `build_retriever`; `DenseRetriever` only sees the protocol.
The manifest check (model id + KB hash) maps to a collection name / schema version there.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

from support_assistant.rag.chunker import MAX_WORDS, OVERLAP_WORDS, Chunk, load_kb
from support_assistant.rag.embedder import Embedder, Vectors


@runtime_checkable
class VectorIndex(Protocol):
    def __len__(self) -> int: ...

    def search(self, query: Vectors, k: int) -> list[tuple[Chunk, float]]:
        """Top-k (chunk, cosine score), best first. `query` is L2-normalised, shape (dim,)."""
        ...


class NumpyIndex:
    def __init__(self, vectors: Vectors, chunks: list[Chunk]) -> None:
        if len(vectors) != len(chunks):
            raise ValueError("vectors and chunks must have the same length")
        self.vectors = vectors
        self.chunks = chunks

    def __len__(self) -> int:
        return len(self.chunks)

    def search(self, query: Vectors, k: int) -> list[tuple[Chunk, float]]:
        if not self.chunks:
            return []
        scores = self.vectors @ query
        k = min(k, len(scores))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [(self.chunks[int(i)], float(scores[i])) for i in top]

    # ---- persistence ----
    def save(self, directory: Path, manifest: dict[str, object]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "vectors.npy", self.vectors)
        (directory / "chunks.json").write_text(
            json.dumps([c.__dict__ for c in self.chunks]), encoding="utf-8"
        )
        # manifest written last: its presence marks a complete index
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path) -> NumpyIndex:
        vectors = np.load(directory / "vectors.npy")
        raw = json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
        return cls(vectors, [Chunk(**c) for c in raw])


def kb_hash(kb_dir: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(kb_dir.glob("*.md")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    h.update(f"{MAX_WORDS}:{OVERLAP_WORDS}".encode())  # chunker params affect the index too
    return h.hexdigest()


def _read_manifest(directory: Path) -> dict[str, object] | None:
    try:
        return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_or_build_index(
    kb_dir: Path, embedder: Embedder, cache_dir: Path, *, force: bool = False
) -> tuple[NumpyIndex, bool]:
    """Return (index, rebuilt). Rebuilds only if the KB content or embedding model changed."""
    manifest = {"model_id": embedder.model_id, "kb_hash": kb_hash(kb_dir)}
    current = _read_manifest(cache_dir)
    if not force and current is not None and all(current.get(k) == v for k, v in manifest.items()):
        try:
            return NumpyIndex.load(cache_dir), False
        except (OSError, ValueError, TypeError):
            pass  # corrupt cache: fall through and rebuild
    chunks = load_kb(kb_dir)
    vectors = embedder.embed_passages([c.embed_text for c in chunks])
    index = NumpyIndex(vectors, chunks)
    index.save(cache_dir, {**manifest, "n_chunks": len(chunks)})
    return index, True
