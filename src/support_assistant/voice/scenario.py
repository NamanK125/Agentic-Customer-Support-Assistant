"""Scenario runner: scripted phone calls against the real coordinator, with real-time clip
injection, optional fault injection, and a full record of what happened.

A scenario is JSON:

    {"name": "...", "description": "...",
     "responder": "scripted" | "pipeline",
     "replies": ["...", ...], "reply_latency_ms": 500,     # scripted only, cycled per turn
     "config": {"filler_after_ms": 800, "barge_in": {"confirm_ms": 480}},   # optional overrides
     "steps": [
        {"say": "order_status_query"},                                  # when idle/listening
        {"say": "wait_wrong_order", "when": "answer_audio_start", "delay_ms": 1500},
        {"noise": "noise_burst", "when": "assistant_audio_start", "delay_ms": 1200},
        {"fault": {"target": "inhouse", "mode": "refuse"}},  {"clear_fault": "inhouse"},
        {"wait_ms": 500}, {"wait_idle": true}
     ],
     "expect": {"barge_in_confirmed": 0, "turns_answered": {">=": 2}, "no_duplicate_turns": true}}

Clips resolve from demo/clips/real/<name>.wav first, then demo/clips/synthetic/<name>.wav; the run
log says which file was used and whether it was a real recording.

Each run writes demo/runs/<scenario>/<timestamp>/: events.jsonl, transcript.md, call.wav (stereo:
left = caller line, right = what the assistant played), latency.json, result.json.

Also here, because both the runner's offline mode and the tests need them: fake providers and a
virtual-time event loop (`run_virtual`) in which `asyncio.sleep` costs no wall time, so a 30 s
call with real pacing runs in well under a second and every timeout is deterministic.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import math
import selectors
import time
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import numpy as np

from support_assistant.voice.audio import (
    ClipInjectionSource,
    RecordingSink,
    read_wav,
    stereo_call,
    write_wav,
)
from support_assistant.voice.contracts import (
    SAMPLE_RATE,
    STT,
    TTS,
    VAD,
    CallEvent,
    Responder,
    Transcript,
    VoiceReply,
    pcm_ms,
)
from support_assistant.voice.coordinator import CallCoordinator, CoordinatorConfig
from support_assistant.voice.vad import BargeInConfig, EndpointerConfig

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[3]
CLIPS_REAL = REPO / "demo" / "clips" / "real"
CLIPS_SYNTHETIC = REPO / "demo" / "clips" / "synthetic"
SCENARIOS_DIR = REPO / "demo" / "scenarios"
RUNS_DIR = REPO / "demo" / "runs"

# The caller lines. record_clips.py prompts with this text; the synthetic clips say exactly it.
CLIPS: dict[str, tuple[str, str]] = {
    "order_status_query": ("say", "Hi, where is my order PN-10002?"),
    "return_query": ("say", "Can I return order PN-10004? I opened the box."),
    "product_question": ("say", "What does error E2 mean on my blood pressure monitor?"),
    "wait_wrong_order": ("say", "Wait, wrong order. I meant PN-10005."),
    "false_start": ("say", "Uh."),
    "backchannel": ("say", "Uh-huh."),
    "talk_to_human": ("say", "I want to talk to a real person, please."),
    "noise_burst": ("noise", "(one sharp clap or a desk knock, ~0.1 s)"),
    "keyboard_clicks": ("noise", "(typing on a keyboard near the mic, ~1-2 s)"),
    "background_noise": ("noise", "(room / street / fan noise, no speech, ~6 s)"),
}

T = TypeVar("T")


# ---- virtual time -----------------------------------------------------------------------------


class VirtualDeadlock(RuntimeError):
    """Every task is waiting and no timer is scheduled: in real time this would hang forever."""


class _VirtualSelector(selectors.DefaultSelector):
    def __init__(self) -> None:
        super().__init__()
        self.loop: VirtualTimeLoop | None = None

    def select(self, timeout: float | None = None):  # type: ignore[override]
        ready = super().select(0)
        if ready:
            return ready
        if timeout is None:
            raise VirtualDeadlock("event loop idle with no timers scheduled")
        if timeout > 0 and self.loop is not None:
            self.loop.advance(timeout)
        return []


class VirtualTimeLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock jumps straight to the next timer when nothing is runnable.
    Only for code that waits on timers and in-loop futures (no threads, no real sockets)."""

    def __init__(self) -> None:
        sel = _VirtualSelector()
        super().__init__(sel)
        sel.loop = self
        self._vtime = 0.0

    def time(self) -> float:
        return self._vtime

    def advance(self, dt: float) -> None:
        self._vtime += dt


def run_virtual(coro: Coroutine[Any, Any, T]) -> T:
    loop = VirtualTimeLoop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _loop_time() -> float:
    return asyncio.get_running_loop().time()


# ---- fakes (offline runs and tests) -----------------------------------------------------------


def tone(ms: float, freq: float = 220.0, amp: float = 0.25) -> bytes:
    """A voiced-looking stand-in for synthesized speech (fundamental + harmonics, syllable AM)."""
    t = np.arange(int(SAMPLE_RATE * ms / 1000)) / SAMPLE_RATE
    w = sum(np.sin(2 * np.pi * freq * h * t) / h for h in (1, 2, 3))
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 4 * t)
    return (w * env * amp / 1.8 * 32767).astype(np.int16).tobytes()


class FakeSTT:
    def __init__(
        self,
        name: str = "fake-stt",
        *,
        latency_ms: float = 300.0,
        text: str | Callable[[bytes], str] = "hello",
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.latency_ms = latency_ms
        self.text = text
        self.error = error
        self.calls: list[bytes] = []
        self.cancelled = 0

    async def transcribe(self, pcm: bytes) -> Transcript:
        self.calls.append(pcm)
        try:
            await asyncio.sleep(self.latency_ms / 1000)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if self.error is not None:
            raise self.error
        text = self.text(pcm) if callable(self.text) else self.text
        return Transcript(text, self.name, self.latency_ms, attempts=(f"{self.name}:ok",))


class FakeTTS:
    """Streams `tone` audio, ~`ms_per_char` of audio per character, in `chunk_ms` chunks, after
    `first_chunk_ms` of "synthesis"; later chunks arrive faster than real time (`rtf`)."""

    def __init__(
        self,
        name: str = "fake-tts",
        *,
        first_chunk_ms: float = 150.0,
        chunk_ms: float = 200.0,
        ms_per_char: float = 60.0,
        rtf: float = 0.25,
        freq: float = 220.0,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.first_chunk_ms = first_chunk_ms
        self.chunk_ms = chunk_ms
        self.ms_per_char = ms_per_char
        self.rtf = rtf
        self.freq = freq
        self.error = error
        self.started: list[str] = []
        self.finished: list[str] = []
        self.closed_early: list[str] = []

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        self.started.append(text)
        done = False
        try:
            await asyncio.sleep(self.first_chunk_ms / 1000)
            if self.error is not None:
                raise self.error
            total = max(self.chunk_ms, len(text) * self.ms_per_char)
            n = max(1, math.ceil(total / self.chunk_ms))
            for i in range(n):
                if i:
                    await asyncio.sleep(self.chunk_ms * self.rtf / 1000)
                yield tone(self.chunk_ms, self.freq)
            done = True
            self.finished.append(text)
        finally:
            if not done:
                self.closed_early.append(text)


class ScriptedResponder:
    """Canned replies per turn (cycled), after `latency_ms`. Records every call."""

    def __init__(
        self,
        replies: list[str] | None = None,
        *,
        latency_ms: float = 500.0,
        error: Exception | None = None,
        greeting: str | None = None,
        greeting_latency_ms: float = 100.0,
    ) -> None:
        self.replies = replies or ["Sure, I can help with that."]
        self.greeting_text = greeting
        self.greeting_latency_ms = greeting_latency_ms
        if greeting is not None:  # the coordinator checks hasattr(responder, "greeting")
            self.greeting = self._greeting
        self.latency_ms = latency_ms
        self.error = error
        self.calls: list[str] = []
        self.completed: list[str] = []

    async def _greeting(self, session_id: str) -> str | None:
        await asyncio.sleep(self.greeting_latency_ms / 1000)
        return self.greeting_text

    async def __call__(self, session_id: str, text: str) -> VoiceReply:
        self.calls.append(text)
        await asyncio.sleep(self.latency_ms / 1000)
        if self.error is not None:
            raise self.error
        reply = self.replies[(len(self.completed)) % len(self.replies)]
        self.completed.append(text)
        return VoiceReply(text=reply, intent="scripted")


class MemoryCanned:
    """Canned prompts rendered once through a TTS into memory (offline runs; never touches the
    on-disk cache that the real CannedAudio keeps per voice)."""

    PHRASES = {
        "didnt_catch": "Sorry, I didn't catch that. Could you say it again?",
        "one_moment": "One moment, let me check that.",
        "trouble": "Sorry, I'm having trouble on my side. Please hold on.",
    }

    def __init__(self) -> None:
        self._audio: dict[str, bytes] = {}

    async def prepare(self, tts: TTS) -> None:
        for k, text in self.PHRASES.items():
            try:
                self._audio[k] = b"".join([c async for c in tts.stream(text)])
            except Exception:  # noqa: BLE001
                self._audio[k] = tone(400, 660)

    def get(self, name: str) -> bytes:
        return self._audio.get(name) or tone(400, 660)


def pipeline_responder(pipeline: Any) -> Responder:
    """Adapt SupportPipeline.handle (never raises; 6-12 s on the in-house LLM) to a Responder."""

    async def respond(session_id: str, text: str) -> VoiceReply:
        r = await pipeline.handle(session_id, text, channel="voice")
        return VoiceReply(
            text=r.text,
            blocked=r.blocked,
            rewritten=r.rewritten,
            intent=r.intent.value if r.intent else None,
            trace_id=r.trace_id,
        )

    return respond


# ---- clips ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Clip:
    name: str
    path: Path
    real: bool
    pcm: bytes
    kind: str
    text: str

    @property
    def ms(self) -> float:
        return pcm_ms(self.pcm)


def resolve_clip(
    name: str, *, real_dir: Path = CLIPS_REAL, synth_dir: Path = CLIPS_SYNTHETIC
) -> Clip:
    kind, text = CLIPS.get(name, ("say", name))
    for d, real in ((real_dir, True), (synth_dir, False)):
        p = d / f"{name}.wav"
        if p.exists():
            return Clip(name, p, real, read_wav(p), kind, text)
    raise FileNotFoundError(f"clip {name!r} not in {real_dir} or {synth_dir}")


# ---- scenario spec and run --------------------------------------------------------------------


def build_config(overrides: dict[str, Any] | None, session_id: str) -> CoordinatorConfig:
    o = dict(overrides or {})
    bi = dataclasses.replace(BargeInConfig(), **o.pop("barge_in", {}))
    ep = dataclasses.replace(EndpointerConfig(), **o.pop("endpointer", {}))
    return CoordinatorConfig(endpointer=ep, barge_in=bi, session_id=session_id, **o)


def apply_sets(config: dict[str, Any], sets: list[str]) -> dict[str, Any]:
    """--set barge_in.confirm_ms=640 style overrides on top of a scenario's config block."""
    out = json.loads(json.dumps(config))
    for s in sets:
        key, _, raw = s.partition("=")
        node = out
        parts = key.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        try:
            node[parts[-1]] = json.loads(raw)
        except json.JSONDecodeError:
            node[parts[-1]] = raw
    return out


@dataclass
class RunOptions:
    providers: str = "real"  # "real" (Groq/local whisper, in-house/Edge TTS) or "fake"
    vad: str = "silero"  # "silero" or "energy"
    responder: str | None = None  # override the scenario's responder
    sets: list[str] = field(default_factory=list)
    out_root: Path = RUNS_DIR
    max_call_s: float = 180.0
    settle_ms: float = 400.0
    tail_ms: float = 1500.0
    label: str | None = None  # appended to the run dir name (e.g. "confirm480")


class _EventLog:
    def __init__(self) -> None:
        self.events: list[CallEvent] = []
        self.changed = asyncio.Event()

    def __call__(self, ev: CallEvent) -> None:
        self.events.append(ev)
        self.changed.set()

    async def wait_for(self, match: Callable[[CallEvent], bool], start: int) -> CallEvent:
        i = start
        while True:
            while i < len(self.events):
                ev = self.events[i]
                i += 1
                if match(ev):
                    return ev
            self.changed.clear()
            await self.changed.wait()


_WHEN_ALIASES: dict[str, Callable[[CallEvent], bool]] = {
    "assistant_audio_start": lambda e: e.kind == "playback_start",
    "answer_audio_start": lambda e: e.kind == "playback_start" and e.data.get("kind") == "answer",
    "filler_start": lambda e: e.kind == "playback_start" and e.data.get("kind") == "filler",
    "greeting_audio_start": lambda e: e.kind == "playback_start" and e.turn == 0,
}


def _when(name: str) -> Callable[[CallEvent], bool]:
    return _WHEN_ALIASES.get(name, lambda e: e.kind == name)


async def _build_providers(
    opts: RunOptions, events: _EventLog, clock_ms: Callable[[], float], source: ClipInjectionSource
) -> tuple[STT, TTS, Any, Any, dict[str, Any]]:
    from support_assistant.config import get_settings
    from support_assistant.voice.faults import FaultInjector

    settings = get_settings()
    injector = FaultInjector(events=events, clock=clock_ms)
    if opts.providers == "real":
        from support_assistant.voice.canned import CannedAudio
        from support_assistant.voice.providers import build_stt, build_tts

        stt = build_stt(settings, injector, events, clock_ms)
        tts = build_tts(settings, injector, events, clock_ms)
        warm = getattr(stt, "warm_up", None)
        if warm is not None:
            await warm()
        canned = CannedAudio(settings)
        await canned.prepare(tts)
        info = {
            "stt": [p.name for p in stt.providers],
            "tts": [p.name for p in tts.providers],
            "canned": dict(canned.rendered_with),
        }
        return stt, tts, canned, injector, info

    # Offline: the real fallback chains and fault wrappers around fake leaf providers, so fault
    # scenarios exercise the same retry/fallback code as a live run.
    from support_assistant.voice.faults import FaultySTT, FaultyTTS
    from support_assistant.voice.resilience import FallbackSTT, FallbackTTS

    def clip_text(pcm: bytes) -> str:
        # what was said: speech clips injected within this utterance's time span
        now = _loop_time()
        span = pcm_ms(pcm) / 1000 + 1.5
        said = [i.label for i in source.injections if now - span <= i.at_s <= now]
        return " ".join(CLIPS[s][1] for s in said if s in CLIPS and CLIPS[s][0] == "say")

    stt = FallbackSTT(
        [
            FaultySTT(FakeSTT("groq-whisper", latency_ms=350, text=clip_text), injector),
            FaultySTT(FakeSTT("local-whisper", latency_ms=900, text=clip_text), injector),
        ],
        timeout_s=settings.stt_timeout_s,
        events=events,
        clock=clock_ms,
    )
    tts = FallbackTTS(
        [
            FaultyTTS(FakeTTS("inhouse", first_chunk_ms=250), injector),
            FaultyTTS(FakeTTS("edge", first_chunk_ms=600, freq=180.0), injector),
        ],
        first_chunk_timeout_s=settings.tts_first_chunk_timeout_s,
        chunk_timeout_s=settings.tts_chunk_timeout_s,
        events=events,
        clock=clock_ms,
    )
    canned = MemoryCanned()
    await canned.prepare(tts)
    info = {
        "stt": ["groq-whisper(fake)", "local-whisper(fake)"],
        "tts": ["inhouse(fake)", "edge(fake)"],
        "canned": "memory",
    }
    return stt, tts, canned, injector, info


async def _build_responder(kind: str, spec: dict[str, Any]) -> tuple[Responder, dict[str, Any]]:
    if kind == "scripted":
        r = ScriptedResponder(
            spec.get("replies"),
            latency_ms=spec.get("reply_latency_ms", 500),
            greeting=spec.get("greeting"),
        )
        return r, {"responder": "scripted", "reply_latency_ms": r.latency_ms}
    if kind == "pipeline":
        from support_assistant.harness.factory import build_pipeline

        pipeline = await asyncio.to_thread(build_pipeline)
        return pipeline_responder(pipeline), {"responder": "pipeline (build_pipeline, voice)"}
    raise ValueError(f"unknown responder {kind!r}")


def _make_vad(kind: str) -> VAD:
    if kind == "silero":
        from support_assistant.voice.vad import SileroVAD

        return SileroVAD()
    from support_assistant.voice.vad import EnergyVAD

    return EnergyVAD()


@dataclass
class RunResult:
    name: str
    run_dir: Path | None
    passed: bool
    expectations: list[dict[str, Any]]
    metrics: dict[str, Any]
    latency: dict[str, Any]
    events: list[CallEvent]


async def run_scenario(spec: dict[str, Any], opts: RunOptions | None = None) -> RunResult:
    opts = opts or RunOptions()
    loop = asyncio.get_running_loop()
    epoch = loop.time()

    def clock_ms() -> float:
        return round((loop.time() - epoch) * 1000, 1)

    name = spec["name"]
    log_ = _EventLog()
    background = spec.get("background")
    bg_clip = resolve_clip(background) if background else None
    source = ClipInjectionSource(background=bg_clip.pcm if bg_clip else None)
    sink = RecordingSink()
    stt, tts, canned, injector, provider_info = await _build_providers(opts, log_, clock_ms, source)
    responder_kind = opts.responder or spec.get("responder", "scripted")
    responder, responder_info = await _build_responder(responder_kind, spec)
    vad = _make_vad(opts.vad)
    config_overrides = apply_sets(spec.get("config", {}), opts.sets)
    session = f"voice-{name}-{uuid.uuid4().hex[:6]}"
    cfg = build_config(config_overrides, session)

    # the call starts now: re-anchor so provider warm-up doesn't count as call time, and keep the
    # warm-up events (canned prompt synthesis, model load) out of the call log
    epoch = loop.time()
    setup_events = [asdict(e) for e in log_.events]
    log_.events.clear()
    coord = CallCoordinator(
        source,
        sink,
        vad,
        stt,
        tts,
        responder,
        canned=canned,
        events=log_,
        clock=loop.time,
        epoch=epoch,
        config=cfg,
    )
    clips_used: dict[str, dict[str, Any]] = {}
    if bg_clip:
        clips_used[bg_clip.name] = {
            "path": str(bg_clip.path.relative_to(REPO)),
            "real": bg_clip.real,
        }
    stop = asyncio.Event()
    timed_out = False

    async def wait_idle() -> None:
        quiet_since: float | None = None
        while True:
            idle = coord.is_idle() and not source.busy
            now = loop.time()
            if idle:
                quiet_since = quiet_since or now
                if (now - quiet_since) * 1000 >= opts.settle_ms:
                    return
            else:
                quiet_since = None
            await asyncio.sleep(0.02)

    def emit(event: str, /, **data: Any) -> None:
        log_(CallEvent(t_ms=clock_ms(), kind=event, data=data))

    async def steps() -> None:
        await asyncio.sleep(0.3)  # let the VAD see some room tone first
        for i, step in enumerate(spec.get("steps", [])):
            start = len(log_.events)
            if "when" in step:
                await log_.wait_for(_when(step["when"]), start)
            elif not step.get("now") and any(
                k in step for k in ("say", "noise", "fault", "clear_fault")
            ):
                await wait_idle()  # steps are conversation beats: by default wait for quiet
            if step.get("delay_ms"):
                await asyncio.sleep(step["delay_ms"] / 1000)
            if "say" in step or "noise" in step:
                clip = resolve_clip(step.get("say") or step["noise"])
                source.inject(clip.pcm, clip.name)
                clips_used[clip.name] = {
                    "path": str(clip.path.relative_to(REPO)),
                    "real": clip.real,
                }
                emit(
                    "inject",
                    step=i,
                    clip=clip.name,
                    kind="say" if "say" in step else "noise",
                    real=clip.real,
                    path=str(clip.path.relative_to(REPO)),
                    duration_ms=round(clip.ms),
                    text=clip.text,
                )
            elif "fault" in step:
                f = dict(step["fault"])
                injector.set(f.pop("target"), f.pop("mode"), **f)
            elif "clear_fault" in step:
                injector.clear(step["clear_fault"])
            elif "wait_ms" in step:
                await asyncio.sleep(step["wait_ms"] / 1000)
            elif "wait_idle" in step:
                await wait_idle()
        await wait_idle()
        await asyncio.sleep(opts.tail_ms / 1000)
        stop.set()

    async def guard() -> None:
        nonlocal timed_out
        await asyncio.sleep(spec.get("max_call_s", opts.max_call_s))
        timed_out = True
        emit("scenario_timeout", after_s=spec.get("max_call_s", opts.max_call_s))
        stop.set()

    emit(
        "scenario_start",
        name=name,
        description=spec.get("description", ""),
        providers=opts.providers,
        vad=opts.vad,
        **provider_info,
        **responder_info,
        config=config_overrides,
    )
    runner_error: list[str] = []

    def on_steps_done(t: asyncio.Task[None]) -> None:
        if not t.cancelled() and t.exception() is not None:
            runner_error.append(repr(t.exception()))
            emit("runner_error", error=repr(t.exception()))
            stop.set()

    step_task = loop.create_task(steps())
    step_task.add_done_callback(on_steps_done)
    guard_task = loop.create_task(guard())
    t_wall = time.perf_counter()
    try:
        await coord.run(until=stop)
    finally:
        step_task.cancel()
        guard_task.cancel()
        await asyncio.gather(step_task, guard_task, return_exceptions=True)
    duration_s = loop.time() - epoch

    metrics = compute_metrics(log_.events, coord)
    metrics["timed_out"] = timed_out
    metrics["runner_error"] = runner_error
    metrics["call_s"] = round(duration_s, 2)
    metrics["wall_s"] = round(time.perf_counter() - t_wall, 2)
    expectations = check_expectations(spec.get("expect", {}), metrics)
    passed = all(e["pass"] for e in expectations) and not timed_out and not runner_error
    latency = coord.latency.report(epoch)

    run_dir = None
    if opts.out_root is not None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = f"-{opts.label}" if opts.label else ""
        run_dir = opts.out_root / name / f"{stamp}{suffix}"
        run_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "scenario": name,
            "description": spec.get("description", ""),
            "providers": opts.providers,
            "provider_chain": provider_info,
            "vad": opts.vad,
            **responder_info,
            "clips": clips_used,
            "all_clips_real": all(c["real"] for c in clips_used.values()) if clips_used else None,
            "config": asdict(cfg),
            "config_overrides": config_overrides,
            "setup_events": setup_events,
        }
        _write_outputs(
            run_dir,
            spec,
            meta,
            log_.events,
            metrics,
            expectations,
            passed,
            latency,
            source,
            sink,
            epoch,
            duration_s,
        )
    return RunResult(name, run_dir, passed, expectations, metrics, latency, log_.events)


# ---- metrics and expectations -----------------------------------------------------------------


def compute_metrics(
    events: list[CallEvent], coord: CallCoordinator | None = None
) -> dict[str, Any]:
    def count(kind: str, **match: Any) -> int:
        return sum(
            1
            for e in events
            if e.kind == kind and all(e.data.get(k) == v for k, v in match.items())
        )

    all_starts = [e.turn for e in events if e.kind == "turn_start"]
    starts = [t for t in all_starts if t != 0]  # turn 0 is the greeting, not a caller turn
    ends: dict[int, list[str]] = {}
    for e in events:
        if e.kind == "turn_end" and e.turn is not None:
            ends.setdefault(e.turn, []).append(e.data.get("outcome"))
    answer_audio: dict[int, int] = {}
    for e in events:
        if e.kind == "playback_start" and e.data.get("kind") == "answer" and e.turn is not None:
            answer_audio[e.turn] = answer_audio.get(e.turn, 0) + 1
    responder_texts = [e.data.get("merged_text") for e in events if e.kind == "stt_done"]
    outcomes = [o for t in starts for o in ends.get(t, [])]
    eou_at = {e.turn: e.t_ms for e in events if e.kind == "eou"}
    end_at = {e.turn: e.t_ms for e in events if e.kind == "turn_end"}
    walls = [end_at[t] - eou_at[t] for t in eou_at if t in end_at]
    # time to resolution: EOU -> the start of the answer audio or of a canned apology for that
    # turn, else the turn's end (merged, shutdown). The filler doesn't count: it is a stall, not a
    # resolution. This is the "never hang" bound; turn wall time also includes reading out a long
    # answer, so it says nothing about hangs.
    resolved_at: dict[int, float] = {}
    for e in events:
        if e.kind == "playback_start" and e.turn is not None and e.data.get("kind") != "filler":
            resolved_at.setdefault(e.turn, e.t_ms)
    resolutions = [
        min(resolved_at.get(t, float("inf")), end_at.get(t, float("inf"))) - eou_at[t]
        for t in eou_at
        if t in end_at or t in resolved_at
    ]
    no_dup = (
        all(len(v) == 1 for v in ends.values())
        and all(n == 1 for n in answer_audio.values())
        and len(all_starts) == len(set(all_starts))
        and count("invariant_violation") == 0
    )
    dropped = [t for t in all_starts if t not in ends or ends[t] == ["shutdown"]]
    return {
        "greeting": (ends.get(0) or [None])[0] if 0 in all_starts else None,
        "turns": len(starts),
        "turns_answered": sum(1 for o in outcomes if o in ("answered", "interrupted")),
        "turns_completed": outcomes.count("answered"),
        "interrupted": outcomes.count("interrupted"),
        "merged": outcomes.count("merged"),
        "outcomes": outcomes,
        "barge_in_confirmed": count("barge_in_confirmed"),
        "barge_in_on_answer": count("barge_in_confirmed", playback_kind="answer"),
        "ducks": count("duck"),
        "unducks": count("unduck"),
        "utterances_ignored": count("utterance_ignored"),
        "vad_rejected": count("vad_rejected"),
        "fillers": count("filler"),
        "stale_dropped": count("stale_dropped"),
        "replies_held": count("reply_held"),
        "canned": [e.data.get("name") for e in events if e.kind == "canned"],
        "stt_providers": [e.data.get("provider") for e in events if e.kind == "stt_done"],
        "stt_texts": responder_texts,
        "fallback_used": [e.data.get("to") for e in events if e.kind == "provider_fallback"],
        "tts_providers_ok": sorted(
            {
                e.data.get("provider")
                for e in events
                if e.kind == "provider_ok" and e.data.get("kind") == "tts"
            }
        ),
        "tts_failed": count("tts_failed"),
        "stt_failed": count("stt_failed"),
        "no_duplicate_turns": no_dup,
        "no_dropped_turns": not dropped,
        "dropped_turns": dropped,
        "max_turn_wall_ms": max(walls) if walls else 0.0,
        "max_time_to_resolution_ms": max(resolutions) if resolutions else 0.0,
        "coordinator_errors": count("coordinator_error"),
        "invariant_violations": count("invariant_violation"),
    }


_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "contains": lambda a, b: b in a,
    "not_contains": lambda a, b: b not in a,
}


def check_expectations(expect: dict[str, Any], metrics: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for key, want in expect.items():
        actual = metrics.get(key)
        if isinstance(want, dict):
            ok = actual is not None and all(_OPS[op](actual, v) for op, v in want.items())
        elif isinstance(actual, list) and not isinstance(want, list):
            ok = want in actual  # e.g. fallback_used: "edge"
        else:
            ok = actual == want
        out.append({"name": key, "expected": want, "actual": actual, "pass": bool(ok)})
    return out


# ---- outputs ----------------------------------------------------------------------------------


def _write_outputs(
    run_dir: Path,
    spec: dict[str, Any],
    meta: dict[str, Any],
    events: list[CallEvent],
    metrics: dict[str, Any],
    expectations: list[dict[str, Any]],
    passed: bool,
    latency: dict[str, Any],
    source: ClipInjectionSource,
    sink: RecordingSink,
    epoch: float,
    duration_s: float,
) -> None:
    with (run_dir / "events.jsonl").open("w") as fh:
        for e in events:
            fh.write(json.dumps(asdict(e), default=str) + "\n")
    (run_dir / "latency.json").write_text(json.dumps(latency, indent=2, default=str))
    result = {**meta, "passed": passed, "expectations": expectations, "metrics": metrics}
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, default=str))

    caller = source.recorded()
    lead = int(max(0.0, ((source.started_at or epoch) - epoch)) * SAMPLE_RATE) * 2
    caller = b"\x00" * lead + caller
    assistant = sink.render(epoch, max(duration_s, pcm_ms(caller) / 1000))
    write_wav(run_dir / "call.wav", stereo_call(caller, assistant), channels=2)
    (run_dir / "transcript.md").write_text(
        render_transcript(spec, meta, events, metrics, expectations, passed, latency)
    )


def _s(ms: float) -> str:
    return f"{ms / 1000:7.2f}s"


def render_transcript(
    spec: dict[str, Any],
    meta: dict[str, Any],
    events: list[CallEvent],
    metrics: dict[str, Any],
    expectations: list[dict[str, Any]],
    passed: bool,
    latency: dict[str, Any],
) -> str:
    L: list[str] = []
    L.append(f"# {spec['name']}: {'PASS' if passed else 'FAIL'}\n")
    L.append(f"{spec.get('description', '')}\n")
    clips = ", ".join(
        f"{k} ({'real' if v['real'] else 'SYNTHETIC'})" for k, v in meta["clips"].items()
    )
    L.append(
        f"- responder: **{meta.get('responder')}**; providers: {meta['providers']} "
        f"{meta['provider_chain'].get('stt')} / {meta['provider_chain'].get('tts')}; VAD: {meta['vad']}"  # noqa: E501
    )
    L.append(f"- clips: {clips or '-'}")
    bi = meta["config"]["barge_in"]
    ep = meta["config"]["endpointer"]
    L.append(
        f"- barge-in: duck {bi['duck_ms']:g} ms, confirm {bi['confirm_ms']:g} ms in "
        f"{bi['window_ms']:g} ms window; endpointer: min speech {ep['min_speech_ms']:g} ms, "
        f"hangover {ep['hangover_ms']:g} ms; filler after {meta['config']['filler_after_ms']:g} ms\n"  # noqa: E501
    )
    L.append("## Timeline\n")
    L.append("| t | who | what |")
    L.append("|---|---|---|")
    for e in events:
        d = e.data
        row = _timeline_row(e, d)
        if row:
            L.append(f"| {_s(e.t_ms)} | {row[0]} | {row[1]} |")
    L.append("\n## Latency (end of caller speech → assistant audio)\n")
    L.append(
        "| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |"
    )
    L.append("|---|---|---|---|---|---|---|---|")
    for row in latency["turns"]:
        b = row["breakdown_ms"]

        def f(v: Any) -> str:
            return "-" if v is None else f"{v:.0f}"

        note = row["excluded"] or row["explain"]
        L.append(
            f"| {row['turn']} | {f(b['to_first_audio'])} ({row['first_audio_kind'] or '-'}) | "
            f"{f(b['to_first_answer_audio'])} | {f(b['hangover'])} | {f(b['stt'])} | "
            f"{f(b['llm'])} | {f(b['tts'])} | {note} |"
        )
    s = latency["summary"]
    for k in ("to_first_audio", "to_first_answer_audio"):
        L.append(f"\n- **{k}**: n={s[k]['n']} p50={s[k]['p50']} ms p95={s[k]['p95']} ms")
    L.append("\n## Expectations\n")
    for x in expectations:
        L.append(
            f"- [{'x' if x['pass'] else ' '}] `{x['name']}` expected {x['expected']!r}, got {x['actual']!r}"  # noqa: E501
        )
    L.append("\n## Counters\n")
    L.append("```json\n" + json.dumps(metrics, indent=1, default=str) + "\n```")
    return "\n".join(L) + "\n"


def _timeline_row(e: CallEvent, d: dict[str, Any]) -> tuple[str, str] | None:
    k = e.kind
    t = f"turn {e.turn}" if e.turn is not None else ""
    if k == "inject":
        src = "real" if d.get("real") else "synthetic"
        what = f'"{d.get("text")}"' if d.get("kind") == "say" else d.get("text")
        return "CALLER", f"{what} [{d.get('clip')}, {src}, {d.get('duration_ms')} ms]"
    if k == "speech_start":
        return (
            "vad",
            f"speech start (seg {d.get('segment')}){' over the answer' if d.get('during_answer') else ''}",  # noqa: E501
        )
    if k == "vad_rejected":
        return "vad", f"rejected blip, {d.get('voiced_ms'):.0f} ms voiced"
    if k == "eou":
        extra = f", merged from turns {d['merged_from']}" if d.get("merged_from") else ""
        bi = ", after barge-in" if d.get("barge_in") else ""
        return "vad", f"**end of utterance** → {t} (hangover {d.get('hangover_ms')} ms{bi}{extra})"
    if k == "utterance_ignored":
        return (
            "vad",
            f"ignored overlapping speech ({d.get('voiced_ms'):.0f} ms voiced): {d.get('reason')}",
        )
    if k == "stt_done":
        return (
            "STT",
            f'{t} [{d.get("provider")}]: "{d.get("merged_text")}" ({d.get("stt_ms")} ms; {", ".join(d.get("attempts") or [])})',  # noqa: E501
        )
    if k == "stt_failed":
        return "STT", f"{t} FAILED: {d.get('error')}"
    if k == "greeting_start":
        return "ASSISTANT", f'turn 0 greeting: "{d.get("text")}"'
    if k == "greeting_skipped":
        return "coord", f"greeting skipped: {d.get('reason')}"
    if k == "reply_ready":
        return "LLM", f'{t} reply: "{d.get("text")}"'
    if k == "reply_held":
        return "coord", f"{t} reply held: caller is speaking"
    if k == "filler":
        return "coord", f'{t} filler "one moment" (no reply after {d.get("after_ms"):g} ms)'
    if k == "playback_start":
        name = f" {d.get('name')}" if d.get("name") else ""
        return "ASSISTANT", f"{t} ▶ {d.get('kind')}{name} starts"
    if k == "duck":
        return "coord", f"duck playback (possible barge-in, {d.get('voiced_ms'):.0f} ms voiced)"
    if k == "unduck":
        return "coord", "un-duck: speech stopped before confirm, playback continues"
    if k == "barge_in_confirmed":
        if d.get("playback_kind") == "answer":
            total = d.get("audio_ms_total")
            of = (
                f" of {total} ms ({d.get('played_fraction', 0):.0%})"
                if total
                else f" (reply still synthesizing, {d.get('audio_ms_synthesized')} ms so far)"
            )
            frac = f"{d.get('answer_played_ms')} ms of answer audio{of}"
            if d.get("sentences_total"):
                frac += f", {d.get('sentences_done')}/{d.get('sentences_total')} sentences"
        else:
            frac = f"{d.get('played_ms')} ms of {d.get('playback_kind')}"
        return "coord", f"✂ **BARGE-IN confirmed** on {t}: cut after {frac}"
    if k == "playback_cancelled":
        return "coord", f"playback cancelled ({d.get('reason')})"
    if k == "turn_merged":
        return (
            "coord",
            f'{t} merges cancelled turn {d.get("cancelled")} (was {d.get("phase")}; carried "{d.get("carried_text") or ""}" + {d.get("carried_audio_ms")} ms audio)',  # noqa: E501
        )
    if k == "turn_end":
        return "coord", f"{t} ends: **{d.get('outcome')}** ({d.get('wall_ms')} ms after EOU)"
    if k == "stale_dropped":
        return "coord", f"stale {d.get('what')} dropped (turn {e.turn})"
    if k in ("fault_injected", "fault_cleared"):
        return "FAULT", f"{k.replace('_', ' ')}: {d}"
    if k == "provider_fallback":
        return (
            "provider",
            f"{d.get('kind')} fallback {d.get('provider')} → {d.get('to')} ({d.get('error')}){' (restarts that TTS request from its start)' if d.get('restart') else ''}",  # noqa: E501
        )
    if k == "provider_error":
        return "provider", f"{d.get('kind')} {d.get('provider')} error: {d.get('error')}"
    if k in (
        "tts_failed",
        "responder_failed",
        "assistant_text_only",
        "canned",
        "coordinator_error",
        "invariant_violation",
        "scenario_timeout",
        "canned_unavailable",
    ):
        return "coord", f"{k}: {json.dumps(d, default=str)[:200]}"
    return None
