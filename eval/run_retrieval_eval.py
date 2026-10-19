# ruff: noqa: E501
"""Retrieval eval: dense embedders vs a BM25 keyword baseline.

Usage: uv run python eval/run_retrieval_eval.py
Reports recall@1/@3, MRR (doc level, answerable queries), p50 query latency, and a similarity
threshold sweep (answered / wrong-answer / out-of-scope-accepted rates). Writes
eval/results/retrieval_<date>.md.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import time
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np

from support_assistant.rag.chunker import Chunk, load_kb
from support_assistant.rag.embedder import Embedder, SentenceTransformerEmbedder
from support_assistant.rag.index import NumpyIndex
from support_assistant.rag.retriever import min_score_for

ROOT = Path(__file__).resolve().parents[1]
MODELS = ["BAAI/bge-small-en-v1.5", "sentence-transformers/all-MiniLM-L6-v2"]
THRESHOLDS = [round(x, 2) for x in np.arange(0.20, 0.86, 0.05)]


class BM25:
    """Okapi BM25 over chunks (keyword baseline; numpy/stdlib only)."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks, self.k1, self.b = chunks, k1, b
        self.docs = [self._tok(c.embed_text) for c in chunks]
        self.avg = sum(map(len, self.docs)) / len(self.docs)
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    @staticmethod
    def _tok(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def search(self, query: str, k: int) -> list[tuple[Chunk, float]]:
        q = self._tok(query)
        scores = []
        for tf, d in zip(self.tf, self.docs, strict=True):
            s = 0.0
            for t in q:
                if t in tf:
                    s += (
                        self.idf[t]
                        * tf[t]
                        * (self.k1 + 1)
                        / (tf[t] + self.k1 * (1 - self.b + self.b * len(d) / self.avg))
                    )
            scores.append(s)
        order = np.argsort(scores)[::-1][:k]
        return [(self.chunks[int(i)], float(scores[i])) for i in order]


def ranked_docs(hits: list[tuple[Chunk, float]]) -> list[str]:
    seen: list[str] = []
    for c, _ in hits:
        if c.doc_id not in seen:
            seen.append(c.doc_id)
    return seen


def metrics(rows: list[dict], results: list[list[tuple[Chunk, float]]]) -> dict[str, float]:
    r1 = r3 = rr = 0.0
    n = 0
    for row, hits in zip(rows, results, strict=True):
        rel = set(row["relevant_doc_ids"])
        if not rel:
            continue
        n += 1
        docs = ranked_docs(hits)
        r1 += bool(rel & set(docs[:1]))
        r3 += bool(rel & set(docs[:3]))
        rr += next((1 / (i + 1) for i, d in enumerate(docs) if d in rel), 0.0)
    return {"recall@1": r1 / n, "recall@3": r3 / n, "mrr": rr / n}


def sweep(rows: list[dict], results: list[list[tuple[Chunk, float]]]) -> list[dict[str, float]]:
    out = []
    ans = [(r, h) for r, h in zip(rows, results, strict=True) if r["relevant_doc_ids"]]
    oos = [h for r, h in zip(rows, results, strict=True) if not r["relevant_doc_ids"]]
    for t in THRESHOLDS:
        accepted = [(r, h) for r, h in ans if h[0][1] >= t]
        wrong = sum(1 for r, h in accepted if h[0][0].doc_id not in r["relevant_doc_ids"])
        out.append(
            {
                "t": t,
                "answered": len(accepted) / len(ans),
                "wrong": wrong / len(ans),
                "oos_accepted": sum(1 for h in oos if h[0][1] >= t) / len(oos),
            }
        )
    return out


def best_threshold(sw: list[dict[str, float]]) -> dict[str, float]:
    """Maximise correct answers minus 2x bad answers (wrong-doc or out-of-scope accepted)."""
    return max(
        sw, key=lambda s: (s["answered"] - s["wrong"]) - 2 * (s["wrong"] + s["oos_accepted"])
    )


def run_dense(model_id: str, chunks: list[Chunk], rows: list[dict], prefix: str | None = None):
    emb: Embedder = SentenceTransformerEmbedder(model_id, query_prefix=prefix)
    index = NumpyIndex(emb.embed_passages([c.embed_text for c in chunks]), chunks)
    emb.embed_query("warmup")
    results, lat = [], []
    for row in rows:
        t0 = time.perf_counter()
        hits = index.search(emb.embed_query(row["query"]), 8)
        lat.append((time.perf_counter() - t0) * 1000)
        results.append(hits)
    return results, statistics.median(lat)


def main() -> None:
    rows = [
        json.loads(line)
        for line in (ROOT / "eval/data/retrieval_eval.jsonl").read_text().splitlines()
        if line.strip()
    ]
    chunks = load_kb(ROOT / "kb")
    n_ans = sum(1 for r in rows if r["relevant_doc_ids"])
    report: dict[str, dict] = {}

    bm = BM25(chunks)
    lat = []
    bm_res = []
    for row in rows:
        t0 = time.perf_counter()
        bm_res.append(bm.search(row["query"], 8))
        lat.append((time.perf_counter() - t0) * 1000)
    report["BM25 (keyword baseline)"] = {**metrics(rows, bm_res), "p50_ms": statistics.median(lat)}

    res, p50 = run_dense(MODELS[0], chunks, rows, prefix="")
    report["bge-small-en-v1.5 (no query instruction)"] = {**metrics(rows, res), "p50_ms": p50}

    sweeps = {}
    misses: dict[str, list[str]] = {}
    for m in MODELS:
        res, p50 = run_dense(m, chunks, rows)
        report[m] = {**metrics(rows, res), "p50_ms": p50}
        sweeps[m] = sweep(rows, res)
        misses[m] = [
            f"`{r['id']}` {r['query']!r}: top1={h[0][0].doc_id} ({h[0][1]:.2f}), want {r['relevant_doc_ids']}"
            for r, h in zip(rows, res, strict=True)
            if r["relevant_doc_ids"] and h[0][0].doc_id not in r["relevant_doc_ids"]
        ]

    lines = [
        f"# Retrieval eval - {date.today().isoformat()}",
        "",
        f"{len(rows)} queries ({n_ans} answerable, {len(rows) - n_ans} out-of-scope), "
        f"{len(chunks)} chunks from {len({c.doc_id for c in chunks})} KB docs. "
        "Doc-level metrics; answerable queries only. Latency = p50 of query embed + search, CPU, warm.",
        "",
        "| Method | recall@1 | recall@3 | MRR | p50 latency (ms) |",
        "|---|---|---|---|---|",
    ]
    for name, m in report.items():
        lines.append(
            f"| {name} | {m['recall@1']:.3f} | {m['recall@3']:.3f} | {m['mrr']:.3f} | {m['p50_ms']:.1f} |"
        )
    for m in MODELS:
        b = best_threshold(sweeps[m])
        lines += [
            "",
            f"## Threshold sweep: {m}",
            "",
            "answered = answerable queries whose top-1 score clears the threshold; wrong = answered "
            "but top-1 doc is not a relevant doc; oos accepted = out-of-scope queries that would "
            "still get an answer.",
            "",
            "| threshold | answered | wrong answer | oos accepted |",
            "|---|---|---|---|",
        ]
        for s in sweeps[m]:
            mark = " **<-**" if s["t"] == b["t"] else ""
            lines.append(
                f"| {s['t']:.2f}{mark} | {s['answered']:.2f} | {s['wrong']:.2f} | {s['oos_accepted']:.2f} |"
            )
        lines += [
            "",
            f"Best by (correct - 2 x bad) objective: {b['t']:.2f}. "
            f"Shipped default in `rag/retriever.py`: {min_score_for(m):.2f} "
            "(chosen at the knee where out-of-scope acceptance drops while most answerable "
            "queries still pass; the LLM and citation check back-stop the rest).",
            "",
            "Top-1 misses:",
        ]
        lines += [f"- {x}" for x in misses[m]] or ["- none"]
    out = ROOT / "eval/results" / f"retrieval_{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
