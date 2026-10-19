"""Embedders. Both return L2-normalised float32 matrices, so dot product == cosine similarity."""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

Vectors = NDArray[np.float32]

BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


@runtime_checkable
class Embedder(Protocol):
    model_id: str

    def embed_passages(self, texts: Sequence[str]) -> Vectors:
        """Sync, CPU-bound: for index builds (scripts, startup)."""
        ...

    def embed_query(self, text: str) -> Vectors:
        """Sync, CPU-bound. Returns shape (dim,)."""
        ...

    async def aembed_query(self, text: str) -> Vectors:
        """Async: runs the sync encode in a worker thread so the event loop never blocks."""
        ...


def _normalise(m: Vectors) -> Vectors:
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    return (m / np.maximum(norms, 1e-12)).astype(np.float32)


class SentenceTransformerEmbedder:
    """Wraps a sentence-transformers model. The model loads lazily on first use."""

    def __init__(self, model_id: str, *, query_prefix: str | None = None) -> None:
        self.model_id = model_id
        if query_prefix is None:
            # bge v1.5 models expect an instruction on queries only (not on passages).
            query_prefix = BGE_QUERY_INSTRUCTION if "bge" in model_id.lower() else ""
        self.query_prefix = query_prefix
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_id)
            return self._model

    def embed_passages(self, texts: Sequence[str]) -> Vectors:
        model = self._load()
        return _normalise(
            np.asarray(model.encode(list(texts), normalize_embeddings=True, batch_size=32))
        )

    def embed_query(self, text: str) -> Vectors:
        model = self._load()
        vec = model.encode([self.query_prefix + text], normalize_embeddings=True)
        return _normalise(np.asarray(vec))[0]

    async def aembed_query(self, text: str) -> Vectors:
        return await asyncio.to_thread(self.embed_query, text)

    async def aembed_passages(self, texts: Sequence[str]) -> Vectors:
        return await asyncio.to_thread(self.embed_passages, texts)


class HashingEmbedder:
    """Deterministic bag-of-words (+bigram) hashing embedder. Offline; for tests only.

    Captures lexical overlap, not semantics, which is enough to exercise the pipeline.
    """

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim
        self.model_id = f"hashing-{dim}"

    def _vec(self, text: str) -> Vectors:
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]
        v = np.zeros(self.dim, dtype=np.float32)
        for g in grams:
            h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        return v

    def embed_passages(self, texts: Sequence[str]) -> Vectors:
        return _normalise(np.stack([self._vec(t) for t in texts]))

    def embed_query(self, text: str) -> Vectors:
        return _normalise(self._vec(text)[None, :])[0]

    async def aembed_query(self, text: str) -> Vectors:
        return await asyncio.to_thread(self.embed_query, text)
