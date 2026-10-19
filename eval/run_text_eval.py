"""Text-side eval against the configured LLM: routing accuracy, guardrail recall / false
positives, and classifier-cache hit rate. Writes eval/results/text_eval_<date>.md + .json.

    uv run python eval/run_text_eval.py [--concurrency 8] [--limit N]

Routing rows go through the input guard first, exactly like production (a routing row the guard
blocks is reported separately, not counted as a correct or wrong route). The routing set is
run twice: the second pass measures the classifier cache.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from support_assistant.contracts import BlockedMessage, CheckedMessage, RawMessage
from support_assistant.harness.factory import build_pipeline

DATA = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "results"


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (DATA / name).open() if line.strip()]


async def bounded(n: int, coros):
    sem = asyncio.Semaphore(n)

    async def run(c):
        async with sem:
            return await c

    return await asyncio.gather(*(run(c) for c in coros))


async def route_row(p, row: dict, sid: str) -> dict:
    raw = RawMessage(session_id=sid, text=row["text"])
    checked = await p.input_guard.check(raw)
    if isinstance(checked, BlockedMessage):
        return {**row, "blocked": True, "predicted": None, "cached": False}
    assert isinstance(checked, CheckedMessage)
    d = await p.router.route(checked)
    return {**row, "blocked": False, "predicted": d.intent.value, "cached": d.cached,
            "degraded": d.rationale.startswith("degraded")}  # fmt: skip


async def guard_row(p, row: dict, i: int) -> dict:
    hist = tuple(row.get("history") or ())
    raw = RawMessage(session_id=f"g{i}", text=row["text"], history=hist)
    out = await p.input_guard.check(raw)
    blocked = isinstance(out, BlockedMessage)
    checker = (out.verdict.checker if blocked else out.verdict.checker) or ""
    return {**row, "predicted": "block" if blocked else "allow", "checker": checker}


def guard_metrics(rows: list[dict]) -> dict:
    tp = sum(r["expected"] == "block" and r["predicted"] == "block" for r in rows)
    fn = sum(r["expected"] == "block" and r["predicted"] == "allow" for r in rows)
    fp = sum(r["expected"] == "allow" and r["predicted"] == "block" for r in rows)
    tn = sum(r["expected"] == "allow" and r["predicted"] == "allow" for r in rows)
    return {
        "n": len(rows), "recall": round(tp / max(tp + fn, 1), 3),
        "false_positive_rate": round(fp / max(fp + tn, 1), 3), "tp": tp, "fn": fn, "fp": fp,
        "tn": tn, "heuristic_only": sum("heuristic" in r["checker"] for r in rows),
        "misses": [r["text"][:90] for r in rows if r["expected"] == "block" and r["predicted"] == "allow"][:8],
        "false_positives": [r["text"][:90] for r in rows if r["expected"] == "allow" and r["predicted"] == "block"][:8],
    }  # fmt: skip


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    p = await asyncio.to_thread(build_pipeline)
    lim = (lambda xs: xs[: args.limit]) if args.limit else (lambda xs: xs)

    t0 = time.perf_counter()
    routing = lim(load("routing_eval.jsonl"))
    first = await bounded(args.concurrency, [route_row(p, r, f"r{i}") for i, r in enumerate(routing)])
    second = await bounded(args.concurrency, [route_row(p, r, f"r2{i}") for i, r in enumerate(routing)])
    t_route = time.perf_counter() - t0

    judged = [r for r in first if not r["blocked"]]
    correct = sum(r["predicted"] == r["expected_intent"] for r in judged)
    per = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for r in judged:
        if r["predicted"] == r["expected_intent"]:
            per[r["expected_intent"]]["tp"] += 1
        else:
            per[r["expected_intent"]]["fn"] += 1
            per[r["predicted"]]["fp"] += 1
    per_intent = {
        k: {"precision": round(v["tp"] / max(v["tp"] + v["fp"], 1), 3),
            "recall": round(v["tp"] / max(v["tp"] + v["fn"], 1), 3)}
        for k, v in sorted(per.items())
    }  # fmt: skip
    confusions = Counter((r["expected_intent"], r["predicted"]) for r in judged
                         if r["predicted"] != r["expected_intent"])  # fmt: skip
    routing_res = {
        "n": len(first), "blocked_by_input_guard": len(first) - len(judged),
        "accuracy": round(correct / max(len(judged), 1), 3),
        "accuracy_unambiguous": round(
            sum(r["predicted"] == r["expected_intent"] for r in judged if not r.get("ambiguous"))
            / max(sum(not r.get("ambiguous") for r in judged), 1), 3),
        "degraded_routes": sum(r.get("degraded", False) for r in first),
        "per_intent": per_intent,
        "top_confusions": [f"{a} -> {b}: {n}" for (a, b), n in confusions.most_common(6)],
        "cache_hit_rate_first_pass": round(sum(r["cached"] for r in first) / max(len(first), 1), 3),
        "cache_hit_rate_second_pass": round(sum(r["cached"] for r in second) / max(len(second), 1), 3),
        "cache_stats": (await p.cache_stats()).model_dump() if await p.cache_stats() else None,
    }  # fmt: skip

    t1 = time.perf_counter()
    inj = lim(load("injection_eval.jsonl"))
    dom = [r for r in lim(load("adversarial_domain.jsonl")) if "expected" in r]
    inj_rows = await bounded(args.concurrency, [guard_row(p, r, i) for i, r in enumerate(inj)])
    dom_rows = await bounded(args.concurrency, [guard_row(p, r, 1000 + i) for i, r in enumerate(dom)])
    t_guard = time.perf_counter() - t1

    result = {
        "date": str(date.today()),
        "model": {"router": p.router._model_id},  # noqa: SLF001
        "routing": routing_res,
        "guard_generic_deepset": guard_metrics(inj_rows),
        "guard_pulsenest_adversarial": guard_metrics(dom_rows),
        "wall_s": {"routing_two_passes": round(t_route, 1), "guards": round(t_guard, 1)},
    }
    OUT.mkdir(exist_ok=True)
    stem = OUT / f"text_eval_{date.today()}"
    stem.with_suffix(".json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
