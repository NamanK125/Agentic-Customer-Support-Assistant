"""The call coordinator: turn-taking for the phone line.

    LISTENING -> USER_SPEAKING -> THINKING -> SPEAKING -> LISTENING
                     ^                          |
                     +------- barge-in ---------+

Race-freedom by construction (the graded part):

1. One owner of state. Every decision is made by a single control loop that reads one inbox:
   audio frames (from the reader task) and results from worker tasks (STT, responder, filler
   timer, playback). Worker tasks never touch coordinator state; they post a message tagged with
   the turn id / playback id they were started for, and the loop decides. Handling one message
   never interleaves with handling another, so "check then act" sequences are atomic.
2. Monotonic ids, stale = dropped. Each accepted utterance gets the next turn id; each playback
   gets the next playback id. A message for a turn that is no longer the active one, or for a
   playback that is no longer current, is dropped and logged (`stale_dropped`). Cancelling a task
   is therefore an optimisation, not a correctness requirement: a result that slips through
   between "task finished" and "task cancelled" cannot be acted on.
3. One terminal outcome per turn. `_end_turn` is the only place a turn ends; it records exactly one
   outcome (answered, interrupted, merged, stt_failed, empty_transcript, responder_failed,
   tts_failed, shutdown) and flags a second attempt as an invariant violation. An answer can be
   queued for playback at most once per turn id (`_answered`). So no turn is answered twice, and
   none disappears without an outcome.
4. One owner of playback. At most one `_Playback` exists; filler, answer and canned prompts for a
   turn are queued on it in order, so two audio streams can never overlap or race for the sink.
5. Merge, don't drop. If a new utterance completes while the previous turn has not started its
   answer (STT/LLM in flight, or reply held), the old turn is cancelled and its transcript (or,
   if STT hadn't finished, its audio) is prepended to the new one: one reply covers both.

Every await on something external has a timeout: STT, responder, each TTS chunk, sink writes and
sink stops. A provider that hangs costs a timeout and a spoken apology, never a stuck call.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol

from support_assistant.voice.contracts import (
    STT,
    TTS,
    VAD,
    AudioSink,
    AudioSource,
    CallEvent,
    EventSink,
    Responder,
    Transcript,
    VoiceReply,
    pcm_ms,
)
from support_assistant.voice.latency import LatencyTracker
from support_assistant.voice.vad import (
    BargeInConfig,
    BargeInDetector,
    Endpointer,
    EndpointerConfig,
    EndpointerEvent,
)

log = logging.getLogger(__name__)

FRAME_S = 0.032


class Canned(Protocol):
    def get(self, name: str) -> bytes: ...


class State(StrEnum):
    LISTENING = "listening"
    USER_SPEAKING = "user_speaking"
    THINKING = "thinking"
    SPEAKING = "speaking"


@dataclass(frozen=True)
class CoordinatorConfig:
    endpointer: EndpointerConfig = field(default_factory=EndpointerConfig)
    barge_in: BargeInConfig = field(default_factory=BargeInConfig)
    session_id: str = "voice-call"
    filler_after_ms: float = 800.0  # EOU -> "one moment" if no reply yet
    stt_timeout_s: float = 20.0  # outer bound; the chain has per-provider timeouts inside
    responder_timeout_s: float = 40.0  # pipeline's own turn budget is 30 s
    greeting_timeout_s: float = 10.0  # responder.greeting(); no greeting if it takes longer
    tts_first_chunk_timeout_s: float = 12.0  # per sentence, outer bound over the chain
    tts_chunk_timeout_s: float = 10.0
    sink_slack_s: float = 2.0  # a write may take its audio duration + this
    stop_timeout_s: float = 1.0
    merge_gap_ms: float = 200.0  # silence inserted between carried and new audio
    # False (default): the whole reply is one TTS stream. The in-house TTS keeps one voice only
    # within a session and does its own clause chunking with eager streaming, so per-sentence
    # requests changed the voice mid-reply and paid a session handshake per sentence. True:
    # one stream per sentence (split_sentences), prefetching the next while the current plays.
    split_replies: bool = False


# ---- inbox messages ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Frame:
    pcm: bytes
    t: float  # end of this frame, on the coordinator clock (frame-index based)
    lag_ms: float  # receipt time - t: how late the loop saw it


@dataclass(frozen=True)
class _SttResult:
    turn: int
    transcript: Transcript | None
    error: str | None


@dataclass(frozen=True)
class _ReplyResult:
    turn: int
    reply: VoiceReply | None
    error: str | None


@dataclass(frozen=True)
class _FillerDue:
    turn: int


@dataclass(frozen=True)
class _PlayMsg:
    play_id: int
    what: Literal["audible", "silent", "first_chunk", "done", "failed"]
    t: float
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _SourceEnded:
    error: str | None


@dataclass(frozen=True)
class _GreetingResult:
    text: str | None
    error: str | None


class _Stop:
    pass


Msg = (
    _Frame
    | _SttResult
    | _ReplyResult
    | _FillerDue
    | _PlayMsg
    | _SourceEnded
    | _GreetingResult
    | _Stop
)

GREETING_TURN = 0


# ---- turn and playback records ----------------------------------------------------------------


@dataclass
class _Turn:
    id: int
    pcm: bytes  # audio to transcribe (carried audio + this utterance)
    prefix: str  # carried transcript from merged turns
    eou_at: float
    phase: Literal["stt", "responding", "held", "speaking", "done"] = "stt"
    text: str | None = None
    reply: VoiceReply | None = None
    merged_from: tuple[int, ...] = ()
    is_greeting: bool = False
    tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    filler_played: bool = False
    answer_audible: bool = False  # the caller has started hearing the answer


_END = object()


@dataclass
class _AnswerJob:
    text: str
    sentences: list[str]  # TTS requests: [text] unless split_replies
    queues: list[asyncio.Queue[Any]] = field(default_factory=list)
    # telemetry written by the producers, read for barge-in reporting only
    received_ms: float = 0.0  # audio synthesized so far
    streams_done: int = 0

    @property
    def audio_ms_total(self) -> float | None:
        """Total reply audio, known only once every stream has finished synthesizing."""
        return self.received_ms if self.streams_done == len(self.sentences) else None


@dataclass
class _Playback:
    id: int
    turn: int | None
    queue: asyncio.Queue[Any]
    task: asyncio.Task[None] | None = None
    producers: list[asyncio.Task[None]] = field(default_factory=list)
    closed: bool = False  # end marker queued
    has_answer: bool = False
    current: str | None = None  # kind of the item currently audible
    # progress, written by the playback task, read for barge-in reporting only
    item_started_at: float | None = None
    answer_played_ms: float = 0.0  # completed chunks only
    chunk_started_at: float | None = None  # write of the current chunk began (None = idle)
    chunk_ms: float = 0.0
    job: _AnswerJob | None = None

    def played_ms(self, now: float) -> float:
        """Answer audio heard so far, including the part of the chunk being played. TTS chunks
        can be a whole sentence (seconds), so counting completed writes alone undercounts."""
        partial = 0.0
        if self.chunk_started_at is not None:
            partial = min(self.chunk_ms, max(0.0, (now - self.chunk_started_at) * 1000))
        return self.answer_played_ms + partial

    sentences_total: int = 0
    sentences_done: int = 0
    chars_total: int = 0
    chars_done: int = 0


class _TTSFailed(Exception):
    pass


_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def split_sentences(text: str, *, min_chars: int = 25) -> list[str]:
    """Sentence chunks for streaming TTS. Very short sentences are glued to the next one so the
    first chunk isn't a lone "Sure." followed by a synthesis gap."""
    raw = [s.strip() for s in _SENT_RE.split(text.strip()) if s.strip()]
    out: list[str] = []
    buf = ""
    for s in raw:
        buf = f"{buf} {s}".strip()
        if len(buf) >= min_chars:
            out.append(buf)
            buf = ""
    if buf:
        if out and len(buf) < min_chars // 2:
            out[-1] = f"{out[-1]} {buf}"
        else:
            out.append(buf)
    return out


def _is_empty_transcript(text: str) -> bool:
    return not re.search(r"[A-Za-z0-9]", text)


class CallCoordinator:
    def __init__(
        self,
        source: AudioSource,
        sink: AudioSink,
        vad: VAD,
        stt: STT,
        tts: TTS,
        responder: Responder,
        *,
        canned: Canned | None = None,
        events: EventSink | None = None,
        clock: Callable[[], float] | None = None,
        epoch: float | None = None,
        config: CoordinatorConfig | None = None,
    ) -> None:
        self._source = source
        self._sink = sink
        self._vad = vad
        self._stt = stt
        self._tts = tts
        self._responder = responder
        self._canned = canned
        self._events = events
        self.cfg = config or CoordinatorConfig()
        self._clock = clock or time.monotonic
        self.epoch = self._clock() if epoch is None else epoch

        self._endpointer = Endpointer(self.cfg.endpointer)
        self._barge = BargeInDetector(self.cfg.barge_in)
        self._inbox: asyncio.Queue[Msg] = asyncio.Queue()
        self._tasks: set[asyncio.Task[Any]] = set()

        self._next_turn = 1
        self._next_play = 1
        self._active: _Turn | None = None
        self._playback: _Playback | None = None
        self._audible = False
        self._audible_kind: str | None = None
        self._ducked = False
        self._state = State.LISTENING

        self.latency = LatencyTracker()
        self.outcomes: dict[int, str] = {}
        self.answered: set[int] = set()
        self.violations: list[str] = []
        self._vad_ms: list[float] = []
        self._max_lag_ms = 0.0
        self._frames = 0

    # ---- public --------------------------------------------------------------------------------

    @property
    def state(self) -> State:
        return self._state

    def is_idle(self) -> bool:
        """Nothing in flight: no active turn, nothing playing, no open speech segment."""
        return (
            self._active is None
            and self._playback is None
            and self._endpointer.segment is None
            and not self._audible
        )

    async def run(self, until: asyncio.Event | float | None = None) -> None:
        """Run the call until `until` (an Event, or seconds of call time) or the source ends."""
        reader = self._spawn(self._read_frames(), "frame-reader")
        stopper: asyncio.Task[Any] | None = None
        if isinstance(until, asyncio.Event):
            stopper = self._spawn(self._stop_on(until), "stopper")
        elif until is not None:
            asyncio.get_running_loop().call_later(until, self._post, _Stop())
        self._emit("call_start", config=_config_dict(self.cfg))
        if hasattr(self._responder, "greeting"):
            self._spawn(self._fetch_greeting(), "greeting")
        try:
            while True:
                msg = await self._inbox.get()
                if isinstance(msg, _Stop):
                    break
                if isinstance(msg, _SourceEnded):
                    self._emit("source_ended", error=msg.error)
                    break
                try:
                    await self._handle(msg)
                except Exception as exc:  # a bug must not kill the call; tests assert on it
                    log.exception("coordinator error handling %s", type(msg).__name__)
                    self._emit("coordinator_error", error=repr(exc), msg=type(msg).__name__)
                self._update_state()
        finally:
            reader.cancel()
            if stopper is not None:
                stopper.cancel()
            await self._shutdown()

    # ---- plumbing ------------------------------------------------------------------------------

    def _now(self) -> float:
        return self._clock()

    def _ms(self, t: float | None) -> float | None:
        return None if t is None else round((t - self.epoch) * 1000, 1)

    def _emit(self, event: str, /, turn: int | None = None, **data: Any) -> None:
        if self._events is None:
            return
        try:
            t_ms = self._ms(self._now()) or 0.0
            self._events(CallEvent(t_ms=t_ms, kind=event, turn=turn, data=data))
        except Exception:  # observability must never break a call
            log.exception("event sink failed for %s", event)

    def _post(self, msg: Msg) -> None:
        self._inbox.put_nowait(msg)

    def _spawn(self, coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _stop_on(self, ev: asyncio.Event) -> None:
        await ev.wait()
        self._post(_Stop())

    async def _read_frames(self) -> None:
        t0: float | None = None
        k = 0
        try:
            async for frame in self._source.frames():
                now = self._now()
                if t0 is None:
                    t0 = now - FRAME_S
                k += 1
                t = t0 + k * FRAME_S
                lag = now - t
                if lag > 0.5 or lag < -0.05:  # dropped frames or a stalled loop: re-anchor
                    self._emit("frame_clock_reanchor", lag_ms=round(lag * 1000, 1))
                    t0, k, t, lag = now - FRAME_S, 1, now, 0.0
                self._post(_Frame(frame, t, lag * 1000))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._post(_SourceEnded(repr(exc)))
            return
        self._post(_SourceEnded(None))

    def _update_state(self) -> None:
        if self._audible:
            s = State.SPEAKING
        elif self._endpointer.in_speech:
            s = State.USER_SPEAKING
        elif self._active is not None:
            s = State.THINKING
        else:
            s = State.LISTENING
        if s != self._state:
            self._state = s
            self._emit("state", state=s.value)

    async def _handle(self, msg: Msg) -> None:
        if isinstance(msg, _Frame):
            await self._on_frame(msg)
        elif isinstance(msg, _SttResult):
            await self._on_stt(msg)
        elif isinstance(msg, _ReplyResult):
            await self._on_reply(msg)
        elif isinstance(msg, _FillerDue):
            self._on_filler_due(msg)
        elif isinstance(msg, _PlayMsg):
            await self._on_play(msg)
        elif isinstance(msg, _GreetingResult):
            await self._on_greeting(msg)

    def _stale(self, what: str, turn: int | None = None, **data: Any) -> None:
        self._emit("stale_dropped", turn=turn, what=what, **data)

    # ---- audio in ------------------------------------------------------------------------------

    async def _on_frame(self, f: _Frame) -> None:
        self._frames += 1
        self._max_lag_ms = max(self._max_lag_ms, f.lag_ms)
        t0 = time.perf_counter()
        prob = self._vad.speech_prob(f.pcm)
        self._vad_ms.append((time.perf_counter() - t0) * 1000)

        events = self._endpointer.push(f.pcm, prob, f.t)
        seg = self._endpointer.segment
        if self._audible:
            if self._audible_kind == "answer":
                # Only the answer is protected by the barge-in bar. Speech over the filler or a
                # canned prompt is an ordinary utterance (it merges with the pending turn), so a
                # short "PN-10005" said over "one moment" is not thrown away as a backchannel.
                if seg is not None:
                    seg.overlapped_answer = True
                for ev in events:  # a segment that closed on this very frame also overlapped
                    ev.segment.overlapped_answer = True
            action = self._barge.push(prob)
            if action == "duck":
                self._sink.set_gain(self.cfg.barge_in.duck_gain)
                self._ducked = True
                self._emit("duck", turn=self._playing_turn(), voiced_ms=self._barge.voiced_ms)
            elif action == "unduck":
                self._sink.set_gain(1.0)
                self._ducked = False
                self._emit(
                    "unduck", turn=self._playing_turn(), reason="speech stopped before confirm"
                )
            elif action == "confirm":
                await self._barge_in(f.t)

        for ev in events:
            await self._on_endpoint(ev)

        held = self._active
        if held is not None and held.phase == "held" and not self._endpointer.in_speech:
            self._emit("reply_released", turn=held.id)
            await self._start_answer(held)

    async def _on_endpoint(self, ev: EndpointerEvent) -> None:
        seg = ev.segment
        if ev.kind == "onset":
            return
        if ev.kind == "speech_start":
            self._emit(
                "speech_start",
                segment=seg.id,
                onset_ms=self._ms(seg.onset_at),
                during_answer=seg.overlapped_answer,
            )
            return
        if ev.kind == "abort":
            self._emit(
                "vad_rejected",
                segment=seg.id,
                voiced_ms=seg.voiced_ms,
                reason="below min_speech_ms",
            )
            return
        # speech_end
        self._emit(
            "speech_end",
            segment=seg.id,
            speech_end_at_ms=self._ms(seg.last_voiced_at),
            voiced_ms=seg.voiced_ms,
            utterance_ms=round(pcm_ms(ev.pcm)),
            forced=ev.forced,
        )
        if seg.overlapped_answer and not seg.barged_in:
            # The caller spoke over the answer without reaching the barge-in bar: a backchannel
            # or noise. It is not a turn; the answer kept playing.
            self._emit(
                "utterance_ignored",
                segment=seg.id,
                voiced_ms=seg.voiced_ms,
                reason="overlapped the answer, barge-in not confirmed",
            )
            return
        await self._accept_utterance(ev)

    # ---- barge-in ------------------------------------------------------------------------------

    def _playing_turn(self) -> int | None:
        return self._playback.turn if self._playback is not None else None

    async def _barge_in(self, t: float) -> None:
        pb = self._playback
        seg = self._endpointer.segment
        if seg is not None:
            seg.barged_in = True
        if pb is None:
            return
        kind = pb.current or "unknown"
        info: dict[str, Any] = {
            "playback_kind": kind,
            "voiced_ms": self._barge.voiced_ms,
            "segment": seg.id if seg else None,
        }
        if pb.has_answer:
            played = pb.played_ms(t)
            total = pb.job.audio_ms_total if pb.job is not None else None
            synthesized = pb.job.received_ms if pb.job is not None else None
            info.update(
                answer_played_ms=round(played),
                # None while the TTS is still streaming: the reply's length isn't known yet, and
                # audio synthesized so far is only a lower bound for it
                audio_ms_total=round(total) if total is not None else None,
                audio_ms_synthesized=round(synthesized) if synthesized is not None else None,
                played_fraction=round(played / total, 3) if total else None,
                chars_total=pb.chars_total,
            )
            if self.cfg.split_replies:
                info.update(sentences_done=pb.sentences_done, sentences_total=pb.sentences_total)
        elif pb.item_started_at is not None:
            info["played_ms"] = round((t - pb.item_started_at) * 1000)
        turn = self._active
        self._emit("barge_in_confirmed", turn=pb.turn, **info)
        await self._stop_playback("barge_in")
        if turn is not None and turn.id == pb.turn and turn.answer_audible:
            self._end_turn(turn, "interrupted", **{k: v for k, v in info.items() if k != "segment"})
        # A barge-in over the filler (even with the answer queued behind it, unheard) leaves the
        # turn open: the caller's utterance, which is confirmed and so will end as a turn, merges
        # with it. Nothing the caller never heard is counted as answered.

    # ---- turns ---------------------------------------------------------------------------------

    async def _accept_utterance(self, ev: EndpointerEvent) -> None:
        seg = ev.segment
        turn_id = self._next_turn
        self._next_turn += 1
        prefix, carry_pcm, merged_from = "", b"", ()
        old = self._active
        if old is not None and old.is_greeting:
            # The caller spoke before the greeting was heard: drop it. Never merged, since its
            # text is the assistant's, not the caller's.
            if self._playback is not None and self._playback.turn == old.id:
                await self._stop_playback("caller_spoke")
            self._end_turn(old, "greeting_cancelled", by=turn_id)
            old = None
        if old is not None:
            if old.answer_audible:  # cannot happen: a confirmed barge-in ends it first
                self._violation(f"turn {old.id} still active with answer audible at new utterance")
                self._end_turn(old, "interrupted")
            else:
                if old.text is not None:
                    prefix = old.text
                else:
                    prefix, carry_pcm = old.prefix, old.pcm
                merged_from = (*old.merged_from, old.id)
                self._emit(
                    "turn_merged",
                    turn=turn_id,
                    cancelled=old.id,
                    phase=old.phase,
                    carried_text=prefix or None,
                    carried_audio_ms=round(pcm_ms(carry_pcm)),
                )
                if self._playback is not None and self._playback.turn == old.id:
                    await self._stop_playback("merged")
                self._end_turn(old, "merged", into=turn_id)
        if self._playback is not None:  # e.g. the tail of a canned prompt: the caller has the floor
            await self._stop_playback("new_turn")

        gap = b"\x00\x00" * int(self.cfg.merge_gap_ms * 16) if carry_pcm else b""
        turn = _Turn(
            id=turn_id,
            pcm=carry_pcm + gap + ev.pcm,
            prefix=prefix,
            eou_at=ev.t,
            merged_from=merged_from,
        )
        self._active = turn
        self.latency.mark(turn_id, "speech_end_at", seg.last_voiced_at)
        self.latency.mark(turn_id, "eou_at", ev.t)
        self._emit(
            "eou",
            turn=turn_id,
            speech_end_at_ms=self._ms(seg.last_voiced_at),
            hangover_ms=round((ev.t - seg.last_voiced_at) * 1000),
            audio_ms=round(pcm_ms(turn.pcm)),
            barge_in=seg.barged_in,
            merged_from=list(merged_from),
        )
        self._emit("turn_start", turn=turn_id)
        turn.tasks.append(self._spawn(self._run_stt(turn_id, turn.pcm), f"stt-{turn_id}"))
        turn.tasks.append(self._spawn(self._filler_timer(turn_id), f"filler-{turn_id}"))

    def _end_turn(self, turn: _Turn, outcome: str, **data: Any) -> None:
        if turn.id in self.outcomes:
            self._violation(f"turn {turn.id} ended twice ({self.outcomes[turn.id]}, {outcome})")
            return
        self.outcomes[turn.id] = outcome
        turn.phase = "done"
        for task in turn.tasks:
            if not task.done():
                task.cancel()
        if self._active is turn:
            self._active = None
        if outcome not in ("answered", "interrupted"):
            self.latency.exclude(turn.id, outcome)
        wall = round((self._now() - turn.eou_at) * 1000)
        self._emit("turn_end", turn=turn.id, outcome=outcome, wall_ms=wall, **data)

    # ---- greeting (turn 0) ---------------------------------------------------------------------

    async def _fetch_greeting(self) -> None:
        try:
            fn = self._responder.greeting  # type: ignore[attr-defined]
            text = await asyncio.wait_for(fn(self.cfg.session_id), self.cfg.greeting_timeout_s)
        except TimeoutError:
            self._post(_GreetingResult(None, f"timed out after {self.cfg.greeting_timeout_s:g}s"))
        except Exception as exc:
            self._post(_GreetingResult(None, f"{type(exc).__name__}: {exc}"))
        else:
            self._post(_GreetingResult(text, None))

    async def _on_greeting(self, m: _GreetingResult) -> None:
        if m.error is not None or not (m.text and m.text.strip()):
            self._emit("greeting_skipped", reason=m.error or "no greeting text")
            return
        if self._active is not None or self._endpointer.in_speech or self._next_turn > 1:
            # The caller already has the floor: greeting them now would talk over them.
            self._emit("greeting_skipped", reason="caller already speaking", text=m.text)
            return
        turn = _Turn(id=GREETING_TURN, pcm=b"", prefix="", eou_at=self._now(), is_greeting=True)
        turn.reply = VoiceReply(text=m.text.strip(), intent="greeting")
        turn.phase = "responding"
        self._active = turn
        self.latency.exclude(GREETING_TURN, "greeting")
        self._emit("greeting_start", turn=GREETING_TURN, text=turn.reply.text)
        self._emit("turn_start", turn=GREETING_TURN, greeting=True)
        await self._start_answer(turn)

    def _violation(self, what: str) -> None:
        self.violations.append(what)
        log.error("invariant violation: %s", what)
        self._emit("invariant_violation", what=what)

    async def _run_stt(self, turn_id: int, pcm: bytes) -> None:
        try:
            tr = await asyncio.wait_for(self._stt.transcribe(pcm), self.cfg.stt_timeout_s)
        except TimeoutError:
            self._post(_SttResult(turn_id, None, f"timed out after {self.cfg.stt_timeout_s:g}s"))
        except Exception as exc:  # STTError or a bug: either way, this turn can't be heard
            self._post(_SttResult(turn_id, None, f"{type(exc).__name__}: {exc}"))
        else:
            self._post(_SttResult(turn_id, tr, None))

    async def _on_stt(self, m: _SttResult) -> None:
        turn = self._active
        if turn is None or turn.id != m.turn or turn.phase != "stt":
            self._stale("stt_result", m.turn, text=m.transcript.text if m.transcript else None)
            return
        now = self._now()
        self.latency.mark(turn.id, "stt_done", now)
        if m.transcript is None:
            self._emit("stt_failed", turn=turn.id, error=m.error)
            self._end_turn(turn, "stt_failed", error=m.error)
            await self._play_canned("didnt_catch", turn.id)
            return
        tr = m.transcript
        text = f"{turn.prefix} {tr.text.strip()}".strip()
        self._emit(
            "stt_done",
            turn=turn.id,
            text=tr.text.strip(),
            merged_text=text,
            provider=tr.provider,
            attempts=list(tr.attempts),
            stt_ms=round(tr.latency_ms),
        )
        if _is_empty_transcript(text):
            self._end_turn(turn, "empty_transcript")
            await self._play_canned("didnt_catch", turn.id)
            return
        turn.text = text
        turn.phase = "responding"
        turn.tasks.append(self._spawn(self._run_responder(turn.id, text), f"reply-{turn.id}"))

    async def _run_responder(self, turn_id: int, text: str) -> None:
        try:
            reply = await asyncio.wait_for(
                self._responder(self.cfg.session_id, text), self.cfg.responder_timeout_s
            )
        except TimeoutError:
            self._post(
                _ReplyResult(turn_id, None, f"timed out after {self.cfg.responder_timeout_s:g}s")
            )
        except Exception as exc:
            self._post(_ReplyResult(turn_id, None, f"{type(exc).__name__}: {exc}"))
        else:
            self._post(_ReplyResult(turn_id, reply, None))

    async def _on_reply(self, m: _ReplyResult) -> None:
        turn = self._active
        if turn is None or turn.id != m.turn or turn.phase != "responding":
            self._stale("reply", m.turn, text=m.reply.text if m.reply else None)
            return
        self.latency.mark(turn.id, "reply_ready", self._now())
        if m.reply is None or not m.reply.text.strip():
            err = m.error or "empty reply"
            self._emit("responder_failed", turn=turn.id, error=err)
            self._end_turn(turn, "responder_failed", error=err)
            await self._play_canned("trouble", turn.id)
            return
        turn.reply = m.reply
        self._emit(
            "reply_ready",
            turn=turn.id,
            text=m.reply.text,
            intent=m.reply.intent,
            blocked=m.reply.blocked,
            rewritten=m.reply.rewritten,
            trace_id=m.reply.trace_id,
        )
        if self._endpointer.in_speech:
            # The caller is talking right now: don't start answering over them. If their speech
            # becomes a turn, this one merges into it; if it was noise, the reply is released.
            turn.phase = "held"
            self._emit("reply_held", turn=turn.id, reason="caller speaking")
            return
        await self._start_answer(turn)

    # ---- filler --------------------------------------------------------------------------------

    async def _filler_timer(self, turn_id: int) -> None:
        await asyncio.sleep(self.cfg.filler_after_ms / 1000)
        self._post(_FillerDue(turn_id))

    def _on_filler_due(self, m: _FillerDue) -> None:
        turn = self._active
        if turn is None or turn.id != m.turn or turn.phase not in ("stt", "responding"):
            return  # answered (or gone) in time: no filler needed; not worth a stale event
        if self._canned is None or turn.filler_played:
            return
        if self._endpointer.in_speech or self._playback is not None:
            self._emit(
                "filler_skipped",
                turn=turn.id,
                reason="caller speaking" if self._endpointer.in_speech else "audio busy",
            )
            return
        turn.filler_played = True
        pb = self._new_playback(turn.id)
        pb.queue.put_nowait(("filler", self._canned.get("one_moment")))
        self._emit("filler", turn=turn.id, after_ms=self.cfg.filler_after_ms)

    # ---- playback ------------------------------------------------------------------------------

    def _new_playback(self, turn_id: int | None) -> _Playback:
        if self._playback is not None:
            self._violation(f"new playback while playback {self._playback.id} is current")
        pb = _Playback(id=self._next_play, turn=turn_id, queue=asyncio.Queue())
        self._next_play += 1
        self._playback = pb
        self._sink.set_gain(1.0)
        self._ducked = False
        pb.task = self._spawn(self._playback_main(pb), f"playback-{pb.id}")
        return pb

    async def _start_answer(self, turn: _Turn) -> None:
        assert turn.reply is not None
        if turn.id in self.answered:
            self._violation(f"turn {turn.id} answer requested twice")
            return
        self.answered.add(turn.id)
        turn.phase = "speaking"
        pb = self._playback
        if pb is not None and (pb.turn != turn.id or pb.closed):
            await self._stop_playback("superseded")
            pb = None
        if pb is None:
            pb = self._new_playback(turn.id)
        text = turn.reply.text
        job = _AnswerJob(text, split_sentences(text) if self.cfg.split_replies else [text])
        pb.job = job
        pb.has_answer = True
        pb.sentences_total = len(job.sentences)
        pb.chars_total = len(job.text)
        self._start_producer(pb, job, 0)  # synthesis starts now, even if the filler is playing
        pb.queue.put_nowait(("answer", job))
        pb.queue.put_nowait(_END)
        pb.closed = True
        self._emit(
            "answer_queued",
            turn=turn.id,
            sentences=job.sentences,
            behind_filler=pb.current == "filler",
        )

    async def _play_canned(self, name: str, turn_id: int | None) -> None:
        if self._canned is None:
            self._emit("canned_unavailable", turn=turn_id, name=name)
            return
        pb = self._playback
        if pb is not None and pb.turn == turn_id and not pb.closed:
            # queue behind the filler of the same (now ended) turn rather than cutting it
            pb.queue.put_nowait(("canned", self._canned.get(name), name))
            pb.queue.put_nowait(_END)
            pb.closed = True
        else:
            if pb is not None:
                await self._stop_playback("superseded")
            pb = self._new_playback(turn_id)
            pb.queue.put_nowait(("canned", self._canned.get(name), name))
            pb.queue.put_nowait(_END)
            pb.closed = True
        self._emit("canned", turn=turn_id, name=name)

    async def _stop_playback(self, reason: str) -> None:
        pb = self._playback
        if pb is None:
            return
        self._playback = None
        self._audible = False
        self._barge.reset()
        if pb.task is not None:
            pb.task.cancel()
        try:
            await asyncio.wait_for(self._sink.stop(), self.cfg.stop_timeout_s)
        except Exception as exc:
            self._emit("sink_error", op="stop", error=repr(exc))
        self._sink.set_gain(1.0)
        self._ducked = False
        # Not awaiting the cancelled task: its cleanup (closing TTS streams) can take a while and
        # must not stall frame processing right after a barge-in. A cancelled task cannot write
        # to the sink again, and anything it posts carries a dead playback id and is dropped.
        self._emit(
            "playback_cancelled", turn=pb.turn, play_id=pb.id, reason=reason, kind=pb.current
        )

    async def _on_play(self, m: _PlayMsg) -> None:
        pb = self._playback
        if pb is None or pb.id != m.play_id:
            self._stale(f"playback_{m.what}", play_id=m.play_id)
            return
        turn = self._active if self._active is not None and self._active.id == pb.turn else None
        if m.what == "first_chunk":
            if turn is not None:
                self.latency.mark(turn.id, "tts_first_chunk", m.t)
            self._emit("tts_first_chunk", turn=pb.turn, at_ms=self._ms(m.t))
        elif m.what == "audible":
            kind = m.data["kind"]
            self._audible = True
            self._audible_kind = kind
            self._barge.reset()
            if turn is not None:
                tl = self.latency.turn(turn.id)
                if tl.first_audio_played is None:
                    self.latency.mark(turn.id, "first_audio_played", m.t)
                    tl.first_audio_kind = kind
                if kind == "answer":
                    turn.answer_audible = True
                    self.latency.mark(turn.id, "first_answer_audio_played", m.t)
            self._emit(
                "playback_start",
                turn=pb.turn,
                kind=kind,
                play_id=pb.id,
                name=m.data.get("name"),
                at_ms=self._ms(m.t),
            )
        elif m.what == "silent":
            self._audible = False
            self._audible_kind = None
            self._barge.reset()
            seg = self._endpointer.segment
            if m.data.get("kind") == "answer" and seg is not None and seg.overlapped_answer:
                # The answer finished while the caller was already talking: there is nothing left
                # to protect, so their speech is judged as an ordinary utterance, not dropped as
                # a backchannel just because it started over the last words.
                seg.overlapped_answer = False
                self._emit("overlap_released", turn=pb.turn, segment=seg.id)
            if self._ducked:
                self._sink.set_gain(1.0)
                self._ducked = False
            self._emit("playback_item_done", turn=pb.turn, kind=m.data.get("kind"))
        elif m.what == "done":
            self._playback = None
            self._audible = False
            self._emit("playback_done", turn=pb.turn, play_id=pb.id)
            if turn is not None and pb.has_answer:
                self._end_turn(turn, "answered", answer_played_ms=round(pb.answer_played_ms))
        elif m.what == "failed":
            self._playback = None
            self._audible = False
            err = m.data.get("error")
            self._emit("tts_failed", turn=pb.turn, error=err, stage=m.data.get("stage"))
            if turn is not None and pb.has_answer:
                # Say what we could not: the text goes to the log/transcript, then an apology.
                self._emit(
                    "assistant_text_only", turn=turn.id, text=turn.reply.text if turn.reply else ""
                )
                self._end_turn(
                    turn, "tts_failed", error=err, answer_played_ms=round(pb.answer_played_ms)
                )
                if m.data.get("stage") != "sink":
                    await self._play_canned("trouble", pb.turn)
            # a failed filler/canned prompt (sink trouble) just ends that playback; the turn goes on

    # -- playback task (owns the sink while it runs; never touches coordinator state) --

    def _start_producer(self, pb: _Playback, job: _AnswerJob, i: int) -> None:
        q: asyncio.Queue[Any] = asyncio.Queue()
        job.queues.append(q)
        notify = i == 0
        pb.producers.append(
            self._spawn(self._produce(pb.id, job, i, q, notify), f"tts-{pb.id}-{i}")
        )

    async def _produce(
        self, play_id: int, job: _AnswerJob, i: int, q: asyncio.Queue[Any], notify: bool
    ) -> None:
        agen = self._tts.stream(job.sentences[i])
        try:
            n = 0
            async for chunk in agen:
                if n == 0 and notify:
                    self._post(_PlayMsg(play_id, "first_chunk", self._now()))
                n += 1
                job.received_ms += pcm_ms(chunk)
                q.put_nowait(chunk)
            job.streams_done += 1
            q.put_nowait(_END)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            q.put_nowait(exc)
        finally:
            aclose = getattr(agen, "aclose", None)
            if aclose is not None:
                try:
                    await asyncio.wait_for(aclose(), self.cfg.stop_timeout_s)
                except Exception:
                    log.debug("tts aclose failed", exc_info=True)

    async def _write(self, pcm: bytes) -> None:
        try:
            await asyncio.wait_for(
                self._sink.write(pcm), pcm_ms(pcm) / 1000 + self.cfg.sink_slack_s
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _TTSFailed(f"sink write failed: {exc!r}") from exc

    async def _playback_main(self, pb: _Playback) -> None:
        try:
            while True:
                item = await pb.queue.get()
                if item is _END:
                    break
                kind = item[0]
                if kind == "answer":
                    await self._play_answer(pb, item[1])
                else:
                    await self._play_pcm(pb, kind, item[1], item[2] if len(item) > 2 else None)
            self._post(_PlayMsg(pb.id, "done", self._now()))
        except asyncio.CancelledError:
            raise
        except _TTSFailed as exc:
            stage = "sink" if "sink write" in str(exc) else "tts"
            self._post(_PlayMsg(pb.id, "failed", self._now(), {"error": str(exc), "stage": stage}))
        except Exception as exc:
            self._post(_PlayMsg(pb.id, "failed", self._now(), {"error": repr(exc), "stage": "bug"}))
        finally:
            for p in pb.producers:
                p.cancel()
            if pb.producers:
                await asyncio.wait(pb.producers, timeout=self.cfg.stop_timeout_s)

    async def _play_pcm(self, pb: _Playback, kind: str, pcm: bytes, name: str | None) -> None:
        pb.current = kind
        pb.item_started_at = self._now()
        self._post(_PlayMsg(pb.id, "audible", pb.item_started_at, {"kind": kind, "name": name}))
        await self._write(pcm)
        self._post(_PlayMsg(pb.id, "silent", self._now(), {"kind": kind}))
        pb.current = None

    async def _play_answer(self, pb: _Playback, job: _AnswerJob) -> None:
        audible = False
        for i, sentence in enumerate(job.sentences):
            q = job.queues[i]
            got = False
            while True:
                limit = self.cfg.tts_chunk_timeout_s if got else self.cfg.tts_first_chunk_timeout_s
                try:
                    item = await asyncio.wait_for(q.get(), limit)
                except TimeoutError:
                    what = "chunk" if got else "first chunk"
                    raise _TTSFailed(f"no {what} for sentence {i + 1} within {limit:g}s") from None
                if item is _END:
                    break
                if isinstance(item, BaseException):
                    raise _TTSFailed(f"{type(item).__name__}: {item}") from item
                if not got:
                    got = True
                    if i + 1 < len(job.sentences) and len(job.queues) == i + 1:
                        self._start_producer(pb, job, i + 1)  # prefetch while this one plays
                if not audible:
                    audible = True
                    pb.current = "answer"
                    pb.item_started_at = self._now()
                    self._post(_PlayMsg(pb.id, "audible", pb.item_started_at, {"kind": "answer"}))
                pb.chunk_started_at, pb.chunk_ms = self._now(), pcm_ms(item)
                await self._write(item)
                pb.chunk_started_at = None
                pb.answer_played_ms += pcm_ms(item)
            pb.sentences_done = i + 1
            pb.chars_done += len(sentence) + 1
        if audible:
            self._post(_PlayMsg(pb.id, "silent", self._now(), {"kind": "answer"}))
        pb.current = None

    # ---- shutdown ------------------------------------------------------------------------------

    async def _shutdown(self) -> None:
        if self._playback is not None:
            await self._stop_playback("shutdown")
        if self._active is not None:
            self._end_turn(self._active, "shutdown", phase=self._active.phase)
        pending = [t for t in self._tasks if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.wait(pending, timeout=2.0)
        vad = sorted(self._vad_ms)
        self._emit(
            "vad_stats",
            frames=self._frames,
            vad_ms_mean=round(sum(vad) / len(vad), 3) if vad else None,
            vad_ms_p95=round(vad[int(0.95 * (len(vad) - 1))], 3) if vad else None,
            vad_ms_max=round(vad[-1], 3) if vad else None,
            max_frame_lag_ms=round(self._max_lag_ms, 1),
        )
        self._emit("call_end", outcomes=dict(self.outcomes), violations=list(self.violations))


def _config_dict(cfg: CoordinatorConfig) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(cfg)
