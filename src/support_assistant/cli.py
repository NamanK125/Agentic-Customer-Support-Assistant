"""`support-assistant` CLI: interactive REPL, or `--once "text"` for a single turn."""

from __future__ import annotations

import argparse
import asyncio
import uuid

from support_assistant.contracts import FinalReply


def _print(reply: FinalReply) -> None:
    flags = [name for name in ("blocked", "rewritten") if getattr(reply, name)]
    print(f"\nassistant> {reply.text}")
    print(
        f"  intent={reply.intent.value if reply.intent else '-'} handler={reply.handler or '-'} "
        f"flags={','.join(flags) or '-'} trace_id={reply.trace_id}"
    )
    if reply.guard_notes:
        print(f"  guard_notes={list(reply.guard_notes)}")


async def _run(args: argparse.Namespace) -> int:
    from support_assistant.harness.factory import build_pipeline
    from support_assistant.observability import setup_telemetry

    setup_telemetry()
    pipeline = await asyncio.to_thread(build_pipeline)
    session_id = args.session or f"cli-{uuid.uuid4().hex[:8]}"
    if args.once is not None:
        _print(await pipeline.handle(session_id, args.once))
        return 0

    print(f"PulseNest support (session {session_id}). Ctrl-D or 'exit' to quit.")
    while True:
        try:
            text = (await asyncio.to_thread(input, "\nyou> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.lower() in {"exit", "quit"}:
            break
        if text:
            _print(await pipeline.handle(session_id, text))
    stats = await pipeline.cache_stats()
    if stats is not None:
        print(f"classifier cache: {stats.hits} hits / {stats.misses} misses ({stats.hit_rate:.0%})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="support-assistant", description=__doc__)
    parser.add_argument("--once", metavar="TEXT", help="run a single turn and exit")
    parser.add_argument("--session", help="session id (default: random)")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
