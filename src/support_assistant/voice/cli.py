"""`support-voice`: a live phone-line session on this machine's mic and speakers.

    uv run python -m support_assistant.voice.cli            # pipeline responder, Silero VAD
    uv run python -m support_assistant.voice.cli --responder scripted --minutes 3

Use headphones: there is no echo cancellation, so on open speakers the assistant hears itself and
barges in on its own voice (see voice/audio.py).

While the call runs, type commands on stdin to demo provider failures live:
    fault inhouse refuse | fault tts hang | fault groq-whisper invalid_key | clear [target] | quit

Everything is logged like a scenario run, to demo/runs/live/<timestamp>/: events.jsonl,
transcript.md, latency.json, caller.wav (what the mic sent).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from support_assistant.voice.audio import MicSource, SpeakerSink, write_wav
from support_assistant.voice.contracts import CallEvent
from support_assistant.voice.coordinator import CallCoordinator
from support_assistant.voice.scenario import (
    RUNS_DIR,
    ScriptedResponder,
    build_config,
    compute_metrics,
    pipeline_responder,
    render_transcript,
)

log = logging.getLogger(__name__)

_CONSOLE = {
    "speech_start": lambda e: "  [you are speaking]",
    "eou": lambda e: f"  [end of utterance -> turn {e.turn}]",
    "stt_done": lambda e: f"you ({e.data.get('provider')}): {e.data.get('merged_text')}",
    "filler": lambda e: "assistant: (one moment...)",
    "reply_ready": lambda e: f"assistant: {e.data.get('text')}",
    "duck": lambda e: "  [duck]",
    "unduck": lambda e: "  [un-duck: ignored]",
    "barge_in_confirmed": lambda e: f"  [BARGE-IN: cut {e.data.get('playback_kind')}]",
    "turn_merged": lambda e: f"  [turn {e.data.get('cancelled')} merged into {e.turn}]",
    "turn_end": lambda e: f"  [turn {e.turn}: {e.data.get('outcome')}, {e.data.get('wall_ms')} ms]",
    "provider_fallback": lambda e: (
        f"  [{e.data.get('kind')} fallback {e.data.get('provider')} -> {e.data.get('to')}]"
    ),
    "tts_failed": lambda e: f"  [TTS failed: {e.data.get('error')}]",
    "stt_failed": lambda e: f"  [STT failed: {e.data.get('error')}]",
    "fault_injected": lambda e: f"  [fault: {e.data}]",
    "fault_cleared": lambda e: f"  [fault cleared: {e.data.get('target')}]",
}


class _Recorder:
    """Source wrapper that keeps a copy of every mic frame (for caller.wav)."""

    def __init__(self, inner: MicSource) -> None:
        self.inner = inner
        self.recorded: list[bytes] = []  # not `frames`: that name is the AudioSource method

    async def frames_iter(self):  # noqa: ANN201
        async for f in self.inner.frames():
            self.recorded.append(f)
            yield f

    def frames(self):  # noqa: ANN201 - AudioSource protocol
        return self.frames_iter()


async def _commands(injector: Any, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            line = await asyncio.to_thread(sys.stdin.readline)
        except Exception:  # noqa: BLE001
            return
        if not line:
            return
        parts = line.split()
        if not parts:
            continue
        try:
            if parts[0] == "quit":
                stop.set()
            elif parts[0] == "fault" and len(parts) >= 3:
                injector.set(parts[1], parts[2])
            elif parts[0] == "clear":
                injector.clear(parts[1] if len(parts) > 1 else None)
            else:
                print("commands: fault <target> <mode> | clear [target] | quit", flush=True)
        except ValueError as exc:
            print(f"  ! {exc}", flush=True)


async def _live(args: argparse.Namespace) -> int:
    from support_assistant.config import get_settings
    from support_assistant.observability import setup_telemetry
    from support_assistant.voice.canned import CannedAudio
    from support_assistant.voice.faults import FaultInjector
    from support_assistant.voice.providers import build_stt, build_tts
    from support_assistant.voice.vad import EnergyVAD, SileroVAD

    setup_telemetry()
    settings = get_settings()
    loop = asyncio.get_running_loop()
    epoch = loop.time()
    run_dir = Path(args.out) / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    events: list[CallEvent] = []
    fh = (run_dir / "events.jsonl").open("w")

    def on_event(ev: CallEvent) -> None:
        events.append(ev)
        fh.write(json.dumps(asdict(ev), default=str) + "\n")
        fh.flush()
        fmt = _CONSOLE.get(ev.kind)
        if fmt is not None:
            print(f"{ev.t_ms / 1000:7.2f}s {fmt(ev)}", flush=True)

    def clock_ms() -> float:
        return round((loop.time() - epoch) * 1000, 1)

    print("starting: loading models and pre-rendering prompts...", flush=True)
    injector = FaultInjector(events=on_event, clock=clock_ms)
    stt = build_stt(settings, injector, on_event, clock_ms)
    tts = build_tts(settings, injector, on_event, clock_ms)
    warm = getattr(stt, "warm_up", None)
    if warm is not None:
        await warm()
    canned = CannedAudio(settings)
    await canned.prepare(tts)
    if args.responder == "pipeline":
        from support_assistant.harness.factory import build_pipeline

        responder = pipeline_responder(await asyncio.to_thread(build_pipeline))
    else:
        responder = ScriptedResponder(
            [
                "Sure. I'm a scripted test reply, and you can interrupt me "
                "at any point to see barge-in working."
            ],
            latency_ms=600,
        )
    vad = SileroVAD() if args.vad == "silero" else EnergyVAD()
    source = _Recorder(MicSource(args.input_device))
    sink = SpeakerSink(args.output_device)
    cfg = build_config({}, args.session or f"voice-live-{uuid.uuid4().hex[:6]}")
    epoch = loop.time()
    coord = CallCoordinator(
        source,
        sink,
        vad,
        stt,
        tts,
        responder,
        canned=canned,
        events=on_event,
        clock=loop.time,
        epoch=epoch,
        config=cfg,
    )
    stop = asyncio.Event()
    cmd_task = loop.create_task(_commands(injector, stop))
    if args.minutes:
        loop.call_later(args.minutes * 60, stop.set)
    print(f"call is live (logs: {run_dir}). Speak; Ctrl-C or 'quit' to hang up.", flush=True)
    try:
        await coord.run(until=stop)
    finally:
        cmd_task.cancel()
        sink.close()
        fh.close()
        metrics = compute_metrics(events, coord)
        latency = coord.latency.report(epoch)
        (run_dir / "latency.json").write_text(json.dumps(latency, indent=2, default=str))
        write_wav(run_dir / "caller.wav", b"".join(source.recorded))
        meta = {
            "clips": {},
            "providers": "real",
            "vad": args.vad,
            "responder": args.responder,
            "config": asdict(cfg),
            "provider_chain": {
                "stt": [p.name for p in stt.providers],
                "tts": [p.name for p in tts.providers],
            },
        }
        spec = {"name": "live", "description": "Live mic session (headphones)."}
        (run_dir / "transcript.md").write_text(
            render_transcript(spec, meta, events, metrics, [], True, latency)
        )
        s = latency["summary"]
        print(
            f"\nturns {metrics['turns']}, outcomes {metrics['outcomes']}, barge-ins "
            f"{metrics['barge_in_confirmed']}; first audio p50 {s['to_first_audio']['p50']} ms, "
            f"first answer audio p50 {s['to_first_answer_audio']['p50']} ms -> {run_dir}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--responder", choices=["pipeline", "scripted"], default="pipeline")
    ap.add_argument("--vad", choices=["silero", "energy"], default="silero")
    ap.add_argument("--input-device", type=int, default=None)
    ap.add_argument("--output-device", type=int, default=None)
    ap.add_argument("--minutes", type=float, default=None)
    ap.add_argument("--session", default=None)
    ap.add_argument("--out", default=str(RUNS_DIR / "live"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    with contextlib.suppress(KeyboardInterrupt):
        return asyncio.run(_live(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
