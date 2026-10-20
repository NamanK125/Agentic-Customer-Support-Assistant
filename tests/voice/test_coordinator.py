"""Coordinator behaviour and race-freedom.

Every test runs a real CallCoordinator (real endpointer, barge-in detector, playback task, real
paced RecordingSink and ClipInjectionSource) inside a virtual-time event loop: `asyncio.sleep`
costs no wall time, so a 20 s call runs in milliseconds and timings are exact and repeatable.
Speech is a voiced tone and the VAD is EnergyVAD, so nothing depends on torch.

The invariants checked after every call (`assert_invariants`):
  - every turn that started ended exactly once, with an outcome;
  - no turn's answer was played twice;
  - no invariant violations or coordinator errors were logged.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest

from support_assistant.voice.audio import ClipInjectionSource, RecordingSink
from support_assistant.voice.contracts import (
    FRAME_MS,
    CallEvent,
    STTError,
    Transcript,
    TTSError,
    VoiceReply,
    pcm_ms,
)
from support_assistant.voice.coordinator import (
    CallCoordinator,
    CoordinatorConfig,
    _ReplyResult,
    _SttResult,
    split_sentences,
)
from support_assistant.voice.latency import LatencyTracker, percentile
from support_assistant.voice.scenario import (
    FakeSTT,
    FakeTTS,
    MemoryCanned,
    ScriptedResponder,
    compute_metrics,
    run_virtual,
    tone,
)
from support_assistant.voice.vad import BargeInConfig, EndpointerConfig, EnergyVAD

LONG_REPLY = (
    "Your order PN-10002 shipped on Monday with BlueDart. "
    "It is out for delivery today and should reach you by evening. "
    "You will get an SMS with the courier's number before it arrives."
)  # ~9.6 s of fake TTS audio at 60 ms/char


def speech(ms: float) -> bytes:
    return tone(ms, freq=180.0, amp=0.3)


def click(ms: float = 120.0) -> bytes:
    rng = np.random.default_rng(1)
    return (rng.normal(0, 9000, int(16 * ms))).clip(-32768, 32767).astype(np.int16).tobytes()


class Call:
    """A coordinator wired to fakes, plus helpers to script the caller."""

    def __init__(
        self,
        *,
        stt: Any = None,
        tts: Any = None,
        responder: Any = None,
        canned: Any = "memory",
        config: CoordinatorConfig | None = None,
    ) -> None:
        self.stt = stt or FakeSTT(text=lambda pcm: f"utt{len(self.stt.calls)}", latency_ms=300)
        self.tts = tts or FakeTTS()
        self.responder = responder or ScriptedResponder([LONG_REPLY], latency_ms=500)
        self._canned = canned
        self.config = config or CoordinatorConfig()
        self.events: list[CallEvent] = []

    async def __aenter__(self) -> Call:
        loop = asyncio.get_running_loop()
        self.source = ClipInjectionSource(room_tone_rms=30)
        self.sink = RecordingSink()
        canned = self._canned
        if canned == "memory":
            canned = MemoryCanned()
            await canned.prepare(FakeTTS("canned", first_chunk_ms=0))
        self.coord = CallCoordinator(
            self.source,
            self.sink,
            EnergyVAD(),
            self.stt,
            self.tts,
            self.responder,
            canned=canned,
            events=self.events.append,
            clock=loop.time,
            config=self.config,
        )
        self._stop = asyncio.Event()
        self.task = loop.create_task(self.coord.run(until=self._stop))
        await asyncio.sleep(0.5)  # room tone so the noise floor settles
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._stop.set()
        await asyncio.wait_for(self.task, 5)

    def now(self) -> float:
        return asyncio.get_running_loop().time()

    def say(self, pcm: bytes, label: str = "speech") -> None:
        self.source.inject(pcm, label)

    async def wait_event(
        self, event: str, /, within: float = 30.0, start: int = 0, **match: Any
    ) -> CallEvent:
        deadline = self.now() + within
        i = start
        while self.now() < deadline:
            while i < len(self.events):
                e = self.events[i]
                i += 1
                if e.kind == event and all(e.data.get(k) == v for k, v in match.items()):
                    return e
            await asyncio.sleep(0.016)
        raise AssertionError(f"no {event} {match} within {within}s; got {self.kinds()}")

    async def idle(self, within: float = 60.0, settle: float = 0.5) -> None:
        deadline = self.now() + within
        quiet = None
        while self.now() < deadline:
            if self.coord.is_idle() and not self.source.busy:
                quiet = quiet or self.now()
                if self.now() - quiet >= settle:
                    return
            else:
                quiet = None
            await asyncio.sleep(0.016)
        raise AssertionError(f"call never went idle; state={self.coord.state} {self.kinds()[-15:]}")

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]

    def of(self, event: str, /, **match: Any) -> list[CallEvent]:
        return [
            e
            for e in self.events
            if e.kind == event and all(e.data.get(k) == v for k, v in match.items())
        ]

    def outcomes(self) -> dict[int, str]:
        return dict(self.coord.outcomes)


def assert_invariants(call: Call, *, allow_shutdown: bool = False) -> None:
    m = compute_metrics(call.events)
    assert m["no_duplicate_turns"], m
    assert m["coordinator_errors"] == 0, call.of("coordinator_error")
    assert m["invariant_violations"] == 0, call.of("invariant_violation")
    if not allow_shutdown:
        assert m["no_dropped_turns"], m
    # each answered turn was answered from exactly one completed responder call
    answered = [t for t, o in call.outcomes().items() if o in ("answered", "interrupted")]
    assert len(answered) == len(set(answered))


def go(test: Callable[[], Any]) -> Any:
    t0 = time.perf_counter()
    out = run_virtual(test())
    assert time.perf_counter() - t0 < 20, "virtual-time test took too long in wall time"
    return out


# ---- the three required scenarios ---------------------------------------------------------------


def test_silence_through_full_playback():
    async def main():
        async with Call() as c:
            c.say(speech(1200))
            await c.wait_event("turn_end", outcome="answered")
            await c.idle()
        assert c.outcomes() == {1: "answered"}
        assert not c.of("barge_in_confirmed") and not c.of("duck")
        # the whole reply is ONE TTS stream (voice continuity; the service chunks it itself)
        assert c.tts.started == [LONG_REPLY]
        # the caller heard the whole reply
        expected_ms = len(LONG_REPLY) * 60
        assert c.sink.heard_ms() == pytest.approx(expected_ms, rel=0.1)
        assert_invariants(c)

    go(main)


def test_noise_burst_does_not_interrupt():
    async def main():
        async with Call() as c:
            c.say(speech(1200))
            await c.wait_event("playback_start", kind="answer")
            await asyncio.sleep(1.2)
            c.say(click(120), "noise_burst")
            await c.wait_event("turn_end")
            await c.idle()
        assert c.outcomes() == {1: "answered"}
        assert not c.of("barge_in_confirmed")
        assert c.of("vad_rejected") or not c.of("speech_start")
        assert_invariants(c)

    go(main)


def test_real_interruption_cuts_playback_and_becomes_one_new_turn():
    async def main():
        async with Call() as c:
            c.say(speech(1200))
            await c.wait_event("playback_start", kind="answer")
            await asyncio.sleep(1.5)
            t_say = c.now()
            interruption = speech(1800)
            c.say(interruption, "wait_wrong_order")
            duck = await c.wait_event("duck")
            barge = await c.wait_event("barge_in_confirmed")
            await c.wait_event("turn_end", outcome="answered")
            await c.idle()
        assert c.outcomes() == {1: "interrupted", 2: "answered"}
        assert barge.data["playback_kind"] == "answer"
        assert 0 < barge.data["answer_played_ms"] < 2500
        # duck ~160 ms, confirm ~480 ms of voiced audio after the caller started
        assert duck.t_ms / 1000 - (t_say - c.coord.epoch) < 0.35
        assert barge.t_ms / 1000 - (t_say - c.coord.epoch) < 0.75
        # the interruption was transcribed once, onset included, not twice
        assert len(c.stt.calls) == 2
        second = pcm_ms(c.stt.calls[1])
        assert 1800 <= second <= 1800 + 320 + 160 + 2 * FRAME_MS
        # the sink really stopped: what was heard of reply 1 ~= what had played at the cut
        assert len(c.sink.stops) >= 1
        assert_invariants(c)

    go(main)


def test_barge_in_reports_audio_played_within_a_long_chunk():
    """The in-house TTS sends a whole sentence as one chunk; the barge-in report must still say
    how much the caller actually heard, not just the completed chunks (that read 0 ms)."""

    async def main():
        tts = FakeTTS(chunk_ms=6000, ms_per_char=60)  # 6 s chunks, like whole-sentence chunks
        async with Call(tts=tts) as c:
            c.say(speech(1200))
            await c.wait_event("playback_start", kind="answer")
            await asyncio.sleep(1.5)
            c.say(speech(1500))
            barge = await c.wait_event("barge_in_confirmed")
            await c.idle()
        assert barge.data["answer_played_ms"] == pytest.approx(1500 + 576, abs=150)
        # fully synthesized before the cut: the total is known, so the fraction is honest
        assert barge.data["audio_ms_total"] == 12000  # FakeTTS: 10.7 s rounded up to 2 x 6 s chunks
        assert barge.data["played_fraction"] == pytest.approx(
            barge.data["answer_played_ms"] / barge.data["audio_ms_total"], abs=0.01
        )
        assert "sentences_done" not in barge.data
        assert_invariants(c)

    go(main)


def test_barge_in_while_reply_still_synthesizing_reports_no_total():
    async def main():
        tts = FakeTTS(chunk_ms=200, rtf=1.2)  # synthesis slower than playback
        async with Call(tts=tts) as c:
            c.say(speech(1200))
            await c.wait_event("playback_start", kind="answer")
            await asyncio.sleep(1.0)
            c.say(speech(1500))
            barge = await c.wait_event("barge_in_confirmed")
            await c.idle()
        assert barge.data["audio_ms_total"] is None and barge.data["played_fraction"] is None
        assert barge.data["audio_ms_synthesized"] >= barge.data["answer_played_ms"]
        assert tts.closed_early == [LONG_REPLY]  # the one stream was cancelled, not finished

    go(main)


def test_split_replies_streams_per_sentence_with_prefetch():
    async def main():
        cfg = CoordinatorConfig(split_replies=True)
        async with Call(config=cfg) as c:
            c.say(speech(1000))
            await c.wait_event("turn_end", outcome="answered")
            await c.idle()
        assert c.tts.started == split_sentences(LONG_REPLY)
        assert len(c.tts.started) == 3
        assert c.sink.heard_ms() == pytest.approx(len(LONG_REPLY) * 60, rel=0.1)
        assert_invariants(c)

    go(main)


# ---- adversarial --------------------------------------------------------------------------------


def test_backchannel_ducks_and_resumes():
    async def main():
        async with Call() as c:
            c.say(speech(1200))
            await c.wait_event("playback_start", kind="answer")
            await asyncio.sleep(2.0)
            c.say(speech(350), "uh-huh")
            await c.wait_event("duck")
            await c.wait_event("unduck")
            await c.wait_event("turn_end")
            await c.idle()
        assert c.outcomes() == {1: "answered"}
        assert not c.of("barge_in_confirmed")
        assert c.of("utterance_ignored")
        assert [g for _, g in c.sink.gain_changes][:2] == [0.3, 1.0]
        assert c.sink.heard_ms() == pytest.approx(len(LONG_REPLY) * 60, rel=0.1)
        assert_invariants(c)

    go(main)


def test_false_start_then_real_interruption_gives_exactly_one_new_turn():
    async def main():
        async with Call() as c:
            c.say(speech(1200))
            await c.wait_event("playback_start", kind="answer")
            await asyncio.sleep(1.0)
            c.say(speech(350), "uh")
            await c.wait_event("unduck")
            await asyncio.sleep(0.4)
            c.say(speech(1800), "wait wrong order")
            await c.wait_event("barge_in_confirmed")
            await c.wait_event("turn_end", outcome="answered")
            await c.idle()
        assert c.outcomes() == {1: "interrupted", 2: "answered"}
        assert len(c.of("barge_in_confirmed")) == 1
        assert len(c.of("utterance_ignored")) == 1
        assert len(c.responder.completed) == 2
        assert_invariants(c)

    go(main)


def test_overlap_at_playback_start_is_a_barge_in_not_a_double_turn():
    async def main():
        async with Call() as c:
            c.say(speech(1200))
            await c.wait_event("playback_start", kind="answer")
            await asyncio.sleep(0.15)
            c.say(speech(1500))
            await c.wait_event("turn_end", outcome="answered", start=0)
            await c.idle()
        assert c.outcomes() == {1: "interrupted", 2: "answered"}
        assert_invariants(c)

    go(main)


def test_keyboard_clicks_during_reply_do_not_interrupt():
    async def main():
        async with Call() as c:
            c.say(speech(1200))
            await c.wait_event("playback_start", kind="answer")
            for _ in range(6):
                await asyncio.sleep(0.25)
                c.say(click(40), "key")
            await c.wait_event("turn_end")
            await c.idle()
        assert c.outcomes() == {1: "answered"}
        assert not c.of("barge_in_confirmed")
        assert_invariants(c)

    go(main)


def test_speech_starting_over_the_last_words_of_the_answer_is_not_dropped():
    """The caller starts talking 200 ms before the answer ends and keeps going: the barge-in bar
    isn't reached while the answer plays, but once it has ended nothing is left to protect, so
    the speech must become a turn rather than be ignored as a backchannel."""

    async def main():
        responder = ScriptedResponder(["Sure, I can help with that."], latency_ms=300)
        async with Call(responder=responder) as c:
            c.say(speech(1000))
            start = await c.wait_event("playback_start", kind="answer")
            answer_ms = len("Sure, I can help with that.") * 60
            await asyncio.sleep(
                answer_ms / 1000 - 0.2 - (c.now() - c.coord.epoch - start.t_ms / 1000)
            )
            c.say(speech(1200))
            await c.wait_event("overlap_released")
            await c.wait_event("turn_end", outcome="answered", start=len(c.events))
            await c.idle()
        assert c.outcomes() == {1: "answered", 2: "answered"}
        assert not c.of("utterance_ignored") and not c.of("barge_in_confirmed")
        assert_invariants(c)

    go(main)


GREETING = "Hello, thanks for calling PulseNest support. How can I help you today?"


def test_greeting_plays_first_then_caller_turn_is_turn_1():
    async def main():
        responder = ScriptedResponder(["Sure."], latency_ms=300, greeting=GREETING)
        async with Call(responder=responder) as c:
            await c.wait_event("turn_end", outcome="answered")  # the greeting, turn 0
            await c.idle()
            c.say(speech(1000))
            await c.wait_event("turn_end", outcome="answered", start=len(c.events))
            await c.idle()
        kinds = c.kinds()
        assert kinds.index("greeting_start") < kinds.index("speech_start")
        assert c.outcomes() == {0: "answered", 1: "answered"}
        assert responder.completed == ["utt1"]  # the greeting never went to the responder
        assert compute_metrics(c.events)["turns"] == 1
        assert_invariants(c)

    go(main)


def test_speech_over_the_greeting_barges_in_and_becomes_turn_1():
    async def main():
        responder = ScriptedResponder(["Let me check PN-10005."], latency_ms=300, greeting=GREETING)
        async with Call(responder=responder) as c:
            await c.wait_event("playback_start", kind="answer")  # greeting audio
            await asyncio.sleep(0.8)
            c.say(speech(1500))
            barge = await c.wait_event("barge_in_confirmed")
            await c.wait_event("turn_end", outcome="answered", start=len(c.events))
            await c.idle()
        assert barge.turn == 0
        assert c.outcomes() == {0: "interrupted", 1: "answered"}
        assert responder.calls == ["utt1"]  # caller's text only, nothing carried from turn 0
        assert len(c.of("playback_start", kind="answer")) == 2  # greeting once, answer once
        assert_invariants(c)

    go(main)


def test_caller_speaking_before_greeting_is_heard_cancels_it_without_merging():
    async def main():
        responder = ScriptedResponder(["Okay."], latency_ms=300, greeting=GREETING)
        responder.greeting_latency_ms = 1500  # the greeting text is slow to arrive
        async with Call(responder=responder) as c:
            c.say(speech(700))  # caller talks first (Call already spent 0.5 s in room tone)
            await c.wait_event("turn_end", outcome="answered")
            await c.idle()
        assert 0 not in c.outcomes() or c.outcomes()[0] == "greeting_cancelled"
        assert c.of("greeting_skipped") or c.of("turn_end", outcome="greeting_cancelled")
        assert responder.calls == ["utt1"]
        assert_invariants(c)

    go(main)


# ---- no double handling: merge, stale results ---------------------------------------------------


def test_new_utterance_while_thinking_merges_and_answers_once():
    async def main():
        texts = iter(["where is my order", "wait, wrong order, PN-10005"])
        stt = FakeSTT(text=lambda pcm: next(texts), latency_ms=300)
        responder = ScriptedResponder(["PN-10005 ships tomorrow."], latency_ms=3000)
        async with Call(stt=stt, responder=responder) as c:
            c.say(speech(1000))
            await c.wait_event("stt_done")  # turn 1 is now with the LLM
            await asyncio.sleep(0.5)
            c.say(speech(1500))
            await c.wait_event("turn_end", outcome="answered")
            await c.idle()
        assert c.outcomes() == {1: "merged", 2: "answered"}
        assert responder.calls == [
            "where is my order",
            "where is my order wait, wrong order, PN-10005",
        ]
        assert responder.completed == [responder.calls[1]]  # the first call was cancelled
        merged = c.of("turn_merged")[0]
        assert merged.data["cancelled"] == 1 and merged.data["phase"] == "responding"
        assert len(c.of("playback_start", kind="answer")) == 1
        assert_invariants(c)

    go(main)


def test_new_utterance_while_stt_in_flight_carries_the_audio():
    async def main():
        stt = FakeSTT(text="both utterances", latency_ms=3000)
        async with Call(stt=stt) as c:
            c.say(speech(1000))
            await c.wait_event("eou")
            await asyncio.sleep(0.2)
            c.say(speech(900))
            await c.wait_event("turn_end", outcome="answered")
            await c.idle()
        assert c.outcomes() == {1: "merged", 2: "answered"}
        assert stt.cancelled == 1
        first, second = (pcm_ms(p) for p in stt.calls)
        assert second > first + 900  # carried audio + gap + new audio, one transcription
        assert c.responder.calls == ["both utterances"]
        assert_invariants(c)

    go(main)


def test_held_reply_merges_when_caller_keeps_talking():
    async def main():
        responder = ScriptedResponder(["ok"], latency_ms=900)
        async with Call(responder=responder) as c:
            c.say(speech(800))
            await c.wait_event("stt_done")
            c.say(speech(2500))  # still talking when the reply lands
            await c.wait_event("reply_held")
            await c.wait_event("turn_end", outcome="answered")
            await c.idle()
        assert c.outcomes() == {1: "merged", 2: "answered"}
        assert c.of("turn_merged")[0].data["phase"] == "held"
        assert len(c.of("playback_start", kind="answer")) == 1
        assert_invariants(c)

    go(main)


def test_held_reply_is_released_when_the_overlap_was_noise():
    async def main():
        responder = ScriptedResponder(["ok then"], latency_ms=700)
        cfg = CoordinatorConfig(filler_after_ms=5000)
        async with Call(responder=responder, config=cfg) as c:
            c.say(speech(800))
            await c.wait_event("stt_done")
            await asyncio.sleep(0.5)
            c.say(speech(300))  # passes min_speech (in_speech) but is short
            await c.wait_event("turn_end")
            await c.idle()
        # a 300 ms utterance while thinking is a real (short) turn: it merges, nothing is lost
        assert sorted(c.outcomes().values()) in (["answered"], ["answered", "merged"])
        assert_invariants(c)

    go(main)


def test_stale_results_are_dropped_not_acted_on():
    async def main():
        async with Call() as c:
            c.say(speech(1000))
            await c.wait_event("turn_end", outcome="answered")
            await c.idle()
            # late results for a finished turn (as if a provider ignored cancellation)
            c.coord._post(_SttResult(1, Transcript("late", "x", 1.0), None))
            c.coord._post(_ReplyResult(1, VoiceReply("late reply"), None))
            c.coord._post(_ReplyResult(99, VoiceReply("future reply"), None))
            await asyncio.sleep(0.2)
        assert len(c.of("stale_dropped")) == 3
        assert len(c.of("playback_start", kind="answer")) == 1
        assert c.responder.completed == ["utt1"]
        assert_invariants(c)

    go(main)


class CancelIgnoringSTT(FakeSTT):
    """A misbehaving provider: swallows cancellation and returns anyway."""

    async def transcribe(self, pcm: bytes) -> Transcript:
        self.calls.append(pcm)
        try:
            await asyncio.sleep(self.latency_ms / 1000)
        except asyncio.CancelledError:
            self.cancelled += 1
            await asyncio.sleep(0.05)
        return Transcript(f"stt{len(self.calls)}", self.name, 1.0)


def test_result_from_a_provider_that_ignores_cancellation_is_dropped():
    async def main():
        stt = CancelIgnoringSTT(latency_ms=3000)
        async with Call(stt=stt) as c:
            c.say(speech(1000))
            await c.wait_event("eou")
            await asyncio.sleep(0.3)
            c.say(speech(800))
            await c.wait_event("turn_end", outcome="answered")
            await c.idle()
        assert stt.cancelled == 1
        assert c.of("stale_dropped", what="stt_result")
        assert c.outcomes() == {1: "merged", 2: "answered"}
        assert c.responder.completed == ["stt2"]
        assert_invariants(c)

    go(main)


SWEEP_COVERAGE = {
    # (second utterance ms, STT latency ms): stages the sweep must reach
    (1100, 300): {"merge:held", "merge:speaking", "barge:filler", "barge:answer", "one_segment"},
    (400, 2000): {"merge:stt", "merge:responding", "merge:held", "ignored:backchannel"},
}


@pytest.mark.parametrize(("second_ms", "stt_ms"), list(SWEEP_COVERAGE))
def test_second_utterance_at_every_stage_never_doubles_or_drops(second_ms, stt_ms):
    """The caller speaks again `delay` after their first utterance, for delays from 0 to 5 s in
    160 ms steps: inside the hangover, during STT, during the LLM, over the filler, while the reply
    is held, while the answer waits for its first TTS chunk, and over the answer itself. Whatever
    the stage: every turn ends exactly once, no answer plays twice, and the last thing the caller
    said is answered (merged or as a new turn), never silently dropped. The test also asserts it
    actually reached each stage, so a timing change can't quietly hollow it out."""

    async def one(delay_ms: int) -> set[str]:
        responder = ScriptedResponder(
            ["Short answer here. And a second sentence too."], latency_ms=1400
        )
        async with Call(
            responder=responder,
            tts=FakeTTS(first_chunk_ms=400),
            # a real question: a bare "hello" (FakeSTT's default) now skips the filler
            stt=FakeSTT(text="where is my order", latency_ms=stt_ms),
        ) as c:
            c.say(speech(1000))
            await c.wait_event("speech_start")
            await asyncio.sleep(1.0 - 0.256 + delay_ms / 1000)
            c.say(speech(second_ms))
            await asyncio.sleep(3.0)
            await c.idle()
        outcomes = c.outcomes()
        assert_invariants(c)
        assert outcomes[max(outcomes)] == "answered", (delay_ms, outcomes, c.kinds())
        assert set(outcomes.values()) <= {"answered", "interrupted", "merged"}
        assert len(c.of("playback_start", kind="answer")) == len(
            [o for o in outcomes.values() if o in ("answered", "interrupted")]
        )
        seen = {f"merge:{e.data['phase']}" for e in c.of("turn_merged")}
        seen |= {f"barge:{e.data['playback_kind']}" for e in c.of("barge_in_confirmed")}
        if c.of("utterance_ignored"):
            seen.add("ignored:backchannel")
        if outcomes == {1: "answered"} and not c.of("utterance_ignored"):
            seen.add("one_segment")  # the pause was shorter than the hangover
        return seen

    async def main() -> set[str]:
        seen: set[str] = set()
        for delay in range(0, 5200, 160):
            seen |= await one(delay)
        return seen

    seen = go(main)
    assert SWEEP_COVERAGE[(second_ms, stt_ms)] <= seen, seen


# ---- filler and latency -------------------------------------------------------------------------


def test_filler_plays_when_reply_is_slow_and_latency_reports_both_totals():
    async def main():
        responder = ScriptedResponder(["Here you go."], latency_ms=2500)
        async with Call(responder=responder) as c:
            c.say(speech(1000))
            await c.wait_event("turn_end", outcome="answered")
            await c.idle()
        starts = [e.data["kind"] for e in c.of("playback_start")]
        assert starts == ["filler", "answer"]
        tl = c.coord.latency.turns[1]
        b = tl.breakdown()
        assert tl.first_audio_kind == "filler"
        eou_to_filler = (tl.first_audio_played - tl.eou_at) * 1000
        assert eou_to_filler == pytest.approx(800, abs=40)
        assert b["to_first_answer_audio"] > b["to_first_audio"] + 1500
        assert b["stt"] == pytest.approx(300, abs=40)
        assert b["llm"] == pytest.approx(2500, abs=40)
        assert b["hangover"] == pytest.approx(c.config.endpointer.hangover_ms, abs=40)
        assert_invariants(c)

    go(main)


def test_no_filler_when_reply_is_fast():
    async def main():
        responder = ScriptedResponder(["Fast."], latency_ms=200)
        async with Call(responder=responder) as c:
            c.say(speech(1000))
            await c.wait_event("turn_end", outcome="answered")
        assert not c.of("filler")
        tl = c.coord.latency.turns[1]
        assert tl.first_audio_kind == "answer"
        assert tl.first_audio_played == tl.first_answer_audio_played
        b = tl.breakdown()
        assert b["tts"] == pytest.approx(150, abs=40)

    go(main)


def test_no_filler_for_small_talk_even_when_reply_is_slow():
    async def main():
        responder = ScriptedResponder(["Hi, how can I help?"], latency_ms=2500)
        stt = FakeSTT(text="Hello.", latency_ms=300)
        async with Call(responder=responder, stt=stt) as c:
            c.say(speech(1000))
            await c.wait_event("turn_end", outcome="answered")
            await c.idle()
        assert [e.data["kind"] for e in c.of("playback_start")] == ["answer"]
        assert [e.data["reason"] for e in c.of("filler_skipped")] == ["small talk"]
        assert_invariants(c)

    go(main)


def test_barge_in_over_filler_stops_filler_and_merges():
    async def main():
        texts = iter(["first", "second"])
        stt = FakeSTT(text=lambda pcm: next(texts), latency_ms=200)
        responder = ScriptedResponder(["answer"], latency_ms=3000)
        async with Call(stt=stt, responder=responder) as c:
            c.say(speech(1000))
            await c.wait_event("playback_start", kind="filler")
            await asyncio.sleep(0.2)
            c.say(speech(1500))
            barge = await c.wait_event("barge_in_confirmed")
            await c.wait_event("turn_end", outcome="answered")
            await c.idle()
        assert barge.data["playback_kind"] == "filler"
        assert c.outcomes() == {1: "merged", 2: "answered"}
        assert responder.completed == ["first second"]
        assert_invariants(c)

    go(main)


def test_latency_tracker_math():
    lt = LatencyTracker()
    lt.mark(1, "speech_end_at", 10.0)
    lt.mark(1, "eou_at", 10.56)
    lt.mark(1, "stt_done", 10.9)
    lt.mark(1, "first_audio_played", 11.36)
    lt.mark(1, "reply_ready", 13.0)
    lt.mark(1, "tts_first_chunk", 13.2)
    lt.mark(1, "first_answer_audio_played", 13.25)
    lt.mark(1, "eou_at", 99.0)  # first mark wins
    b = lt.turns[1].breakdown()
    assert b == {
        "hangover": 560.0,
        "stt": 340.0,
        "llm": 2100.0,
        "tts": 200.0,
        "playout": 50.0,
        "to_first_audio": 1360.0,
        "to_first_answer_audio": 3250.0,
    }
    assert "llm" in lt.turns[1].explain()
    lt.mark(2, "speech_end_at", 0.0)
    lt.exclude(2, "merged")
    rep = lt.report()
    assert rep["summary"]["to_first_answer_audio"]["n"] == 1
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10


# ---- failures: never hang, always say something -------------------------------------------------


def test_stt_failure_plays_didnt_catch_and_listens_again():
    async def main():
        stt = FakeSTT(error=STTError("fallback-stt", "all STT providers failed", retryable=False))
        async with Call(stt=stt) as c:
            c.say(speech(1000))
            await c.wait_event("turn_end", outcome="stt_failed")
            await c.wait_event("playback_start", kind="canned", name="didnt_catch")
            await c.idle()
            stt.error = None
            c.say(speech(1000))
            await c.wait_event("turn_end", outcome="answered")
            await c.idle()
        assert c.outcomes() == {1: "stt_failed", 2: "answered"}
        assert_invariants(c)

    go(main)


def test_empty_transcript_is_not_sent_to_the_llm():
    async def main():
        async with Call(stt=FakeSTT(text=" . ")) as c:
            c.say(speech(1000))
            await c.wait_event("turn_end", outcome="empty_transcript")
            await c.idle()
        assert c.responder.calls == []
        assert c.of("canned", name="didnt_catch")

    go(main)


def test_tts_failure_plays_trouble_and_logs_text():
    async def main():
        tts = FakeTTS(error=TTSError("fallback-tts", "all TTS providers failed", retryable=False))
        async with Call(tts=tts) as c:
            c.say(speech(1000))
            await c.wait_event("turn_end", outcome="tts_failed")
            await c.wait_event("playback_start", kind="canned", name="trouble")
            await c.idle()
        assert c.of("assistant_text_only")[0].data["text"] == LONG_REPLY
        assert_invariants(c)

    go(main)


def test_tts_failure_with_no_canned_audio_still_ends_the_turn():
    async def main():
        tts = FakeTTS(error=TTSError("fallback-tts", "down", retryable=False))
        async with Call(tts=tts, canned=None) as c:
            c.say(speech(1000))
            await c.wait_event("turn_end", outcome="tts_failed")
            await c.idle()
        assert c.of("assistant_text_only")
        assert c.of("canned_unavailable")

    go(main)


def test_responder_error_and_timeout_play_trouble():
    async def main():
        cfg = CoordinatorConfig(responder_timeout_s=3.0)
        responder = ScriptedResponder(latency_ms=10_000_000)  # effectively hangs
        async with Call(responder=responder, config=cfg) as c:
            c.say(speech(1000))
            end = await c.wait_event("turn_end", outcome="responder_failed")
            await c.wait_event("canned", name="trouble")
            await c.idle()
        assert end.data["wall_ms"] < 3500
        assert "timed out" in end.data["error"]

    go(main)


class HangingSTT(FakeSTT):
    async def transcribe(self, pcm: bytes) -> Transcript:
        self.calls.append(pcm)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class HangingTTS(FakeTTS):
    async def stream(self, text: str):
        self.started.append(text)
        await asyncio.Event().wait()
        yield b""


def test_hanging_stt_is_bounded_by_timeout():
    async def main():
        cfg = CoordinatorConfig(stt_timeout_s=4.0)
        async with Call(stt=HangingSTT(), config=cfg) as c:
            c.say(speech(1000))
            end = await c.wait_event("turn_end", outcome="stt_failed", within=10)
            await c.idle()
        assert end.data["wall_ms"] <= 4100

    go(main)


def test_hanging_tts_is_bounded_by_timeout():
    async def main():
        cfg = CoordinatorConfig(tts_first_chunk_timeout_s=3.0)
        async with Call(tts=HangingTTS(), config=cfg) as c:
            c.say(speech(1000))
            end = await c.wait_event("turn_end", outcome="tts_failed", within=15)
            await c.idle()
        assert c.of("canned", name="trouble")
        assert end.data["wall_ms"] < 3000 + 500 + 300 + 200  # first-chunk bound + llm + stt

    go(main)


def test_hang_faults_through_the_real_fallback_chains_never_hang_the_call():
    faults = pytest.importorskip("support_assistant.voice.faults")
    resilience = pytest.importorskip("support_assistant.voice.resilience")

    async def main():
        inj = faults.FaultInjector()
        stt = resilience.FallbackSTT(
            [
                faults.FaultySTT(FakeSTT("groq-whisper"), inj),
                faults.FaultySTT(FakeSTT("local-whisper"), inj),
            ],
            timeout_s=2.0,
        )
        tts = resilience.FallbackTTS(
            [faults.FaultyTTS(FakeTTS("inhouse"), inj), faults.FaultyTTS(FakeTTS("edge"), inj)],
            first_chunk_timeout_s=1.5,
            chunk_timeout_s=1.5,
        )
        inj.set("stt", "hang")
        async with Call(stt=stt, tts=tts) as c:
            c.say(speech(1000))
            await c.wait_event("turn_end", outcome="stt_failed", within=30)
            await c.idle()
            inj.clear("stt")
            inj.set("tts", "hang")
            c.say(speech(1000))
            await c.wait_event("turn_end", outcome="tts_failed", within=30)
            await c.idle()
            inj.clear("tts")
            inj.set("inhouse", "refuse")
            c.say(speech(1000))
            await c.wait_event("turn_end", outcome="answered", within=60)
            await c.idle()
        assert c.outcomes() == {1: "stt_failed", 2: "tts_failed", 3: "answered"}
        walls = [e.data["wall_ms"] for e in c.of("turn_end")]
        assert walls[0] < 2 * (2.0 * 2 + 0.15) * 1000 + 500  # 2 providers x (1 retry) x timeout
        assert walls[1] < 2 * 1.5 * 1000 + 1500
        assert_invariants(c)

    go(main)


# ---- small pieces -------------------------------------------------------------------------------


def test_split_sentences_glues_short_ones():
    assert split_sentences("Sure. Your order PN-10002 shipped Monday. It arrives today.") == [
        "Sure. Your order PN-10002 shipped Monday.",
        "It arrives today.",
    ]
    assert split_sentences("Okay.") == ["Okay."]


def test_recording_sink_stop_truncates_and_unblocks_write():
    async def main():
        sink = RecordingSink()
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        writer = loop.create_task(sink.write(tone(2000)))
        await asyncio.sleep(0.5)
        await sink.stop()
        await asyncio.wait_for(writer, 0.1)
        assert sink.heard_ms() == pytest.approx(500, abs=FRAME_MS + 1)
        pcm = sink.render(t0, 2.0)
        heard = np.abs(np.frombuffer(pcm, dtype=np.int16))
        assert heard[: 16 * 450].max() > 0 and heard[16 * 550 :].max() == 0

    go(main)


def test_clip_source_is_paced_without_drift():
    async def main():
        src = ClipInjectionSource()
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        n = 0
        async for _ in src.frames():
            n += 1
            if n == 300:
                break
        assert loop.time() - t0 == pytest.approx(300 * FRAME_MS / 1000, abs=1e-6)

    go(main)


def test_endpointer_and_barge_config_are_used():
    cfg = CoordinatorConfig(
        endpointer=EndpointerConfig(hangover_ms=800), barge_in=BargeInConfig(confirm_ms=900)
    )

    async def main():
        async with Call(config=cfg) as c:
            c.say(speech(1000))
            eou = await c.wait_event("eou")
            await c.wait_event("playback_start", kind="answer")
            c.say(speech(700))  # would confirm at 480, not at 900
            await c.wait_event("turn_end")
            await c.idle()
        assert eou.data["hangover_ms"] == pytest.approx(800, abs=40)
        assert not c.of("barge_in_confirmed")

    go(main)
