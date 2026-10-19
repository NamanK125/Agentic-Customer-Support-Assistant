"""Run voice turn-taking scenarios and write their logs to demo/runs/<scenario>/<timestamp>/.

    uv run python scripts/run_scenario.py 03_real_interruption
    uv run python scripts/run_scenario.py all --providers fake --virtual        # offline, instant
    uv run python scripts/run_scenario.py 05 --set barge_in.confirm_ms=640 --label confirm640
    uv run python scripts/run_scenario.py 03 --responder scripted               # isolate barge-in

--providers real   Groq Whisper -> local faster-whisper, in-house TTS -> Edge TTS (default)
--providers fake   the same fallback chains and fault wrappers around fake leaf providers
--virtual          virtual-time event loop (fake providers + scripted responder only): a whole
                   scenario runs in well under a second, with exactly the same coordinator code
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from support_assistant.voice.scenario import (
    SCENARIOS_DIR,
    RunOptions,
    RunResult,
    run_scenario,
    run_virtual,
)


def _resolve(names: list[str]) -> list[Path]:
    files = sorted(SCENARIOS_DIR.glob("*.json"))
    if names == ["all"]:
        return files
    out = []
    for n in names:
        p = Path(n)
        if p.suffix == ".json" and p.exists():
            out.append(p)
            continue
        hits = [f for f in files if f.stem == n or f.stem.startswith(n)]
        if not hits:
            sys.exit(f"no scenario matches {n!r}; have: {', '.join(f.stem for f in files)}")
        out.extend(hits)
    return out


def _summary(r: RunResult) -> str:
    m = r.metrics
    s = r.latency["summary"]
    fa, faa = s["to_first_audio"], s["to_first_answer_audio"]
    failed = [e["name"] for e in r.expectations if not e["pass"]]
    return (
        f"{'PASS' if r.passed else 'FAIL'}  {r.name}: turns={m['turns']} outcomes={m['outcomes']} "
        f"barge_in={m['barge_in_confirmed']} ducks={m['ducks']} ignored={m['utterances_ignored']} "
        f"merged={m['merged']} fallbacks={m['fallback_used']}\n"
        f"      latency first audio p50/p95={fa['p50']}/{fa['p95']} ms, "
        f"first answer audio p50/p95={faa['p50']}/{faa['p95']} ms (n={faa['n']}); "
        f"call {m['call_s']} s, wall {m['wall_s']} s"
        + (f"\n      failed: {failed}" if failed else "")
        + (f"\n      -> {r.run_dir}" if r.run_dir else "")
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("scenarios", nargs="+", help="names/prefixes, paths, or 'all'")
    ap.add_argument("--providers", choices=["real", "fake"], default="real")
    ap.add_argument("--vad", choices=["silero", "energy"], default="silero")
    ap.add_argument("--responder", choices=["scripted", "pipeline"], default=None)
    ap.add_argument("--virtual", action="store_true", help="virtual time (fake providers only)")
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--label", default=None, help="suffix for the run directory name")
    ap.add_argument("--out", type=Path, default=None, help="runs root (default demo/runs)")
    ap.add_argument("--no-write", action="store_true", help="don't write a run directory")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    if args.virtual and args.providers != "fake":
        ap.error("--virtual needs --providers fake (real providers use real sockets and threads)")

    ok = True
    for path in _resolve(args.scenarios):
        spec = json.loads(path.read_text())
        responder = args.responder
        if args.virtual and (responder or spec.get("responder")) == "pipeline":
            responder = "scripted"  # the real pipeline can't run in virtual time
        opts = RunOptions(
            providers=args.providers,
            vad=args.vad,
            responder=responder,
            sets=args.sets,
            label=args.label,
        )
        if args.out is not None:
            opts.out_root = args.out
        if args.no_write:
            opts.out_root = None  # type: ignore[assignment]
        coro = run_scenario(spec, opts)
        result = run_virtual(coro) if args.virtual else asyncio.run(coro)
        print(_summary(result), flush=True)
        ok &= result.passed
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
