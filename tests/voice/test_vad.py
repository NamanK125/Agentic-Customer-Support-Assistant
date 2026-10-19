"""Endpointer, barge-in detector and VADs. Pure state machines: frames and timestamps in, events
out. Silero tests run only with RUN_VOICE_LIVE=1 (torch model load)."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from support_assistant.voice.audio import read_wav
from support_assistant.voice.contracts import FRAME_BYTES, FRAME_MS, FRAME_SAMPLES
from support_assistant.voice.scenario import tone
from support_assistant.voice.vad import (
    BargeInConfig,
    BargeInDetector,
    Endpointer,
    EndpointerConfig,
    EnergyVAD,
)

F = FRAME_MS / 1000
SYNTH = Path(__file__).resolve().parents[2] / "demo" / "clips" / "synthetic"


def frame(tag: int) -> bytes:
    """A distinguishable frame (first sample = tag), so tests can see which frames were kept."""
    a = np.zeros(FRAME_SAMPLES, dtype=np.int16)
    a[0] = tag
    return a.tobytes()


def feed(ep: Endpointer, probs: list[float], start_idx: int = 0):
    out = []
    for i, p in enumerate(probs, start=start_idx):
        out += ep.push(frame(i), p, (i + 1) * F)
    return out


def frames_of(ms: float) -> int:
    return int(round(ms / FRAME_MS))


CFG = EndpointerConfig(min_speech_ms=256, hangover_ms=544, pre_roll_ms=320, tail_ms=160)


def test_speech_end_waits_for_hangover_and_reports_last_voiced_frame():
    ep = Endpointer(CFG)
    probs = [0.0] * 20 + [0.9] * 30 + [0.0] * 40
    evs = feed(ep, probs)
    kinds = [e.kind for e in evs]
    assert kinds == ["onset", "speech_start", "speech_end"]
    end = evs[-1]
    last_voiced_idx = 20 + 30 - 1
    assert end.segment.last_voiced_at == pytest.approx((last_voiced_idx + 1) * F)
    # decided exactly hangover_ms after the last voiced frame
    assert end.t - end.segment.last_voiced_at == pytest.approx(frames_of(544) * F)


def test_speech_start_after_min_speech_and_onset_at_first_voiced_frame():
    ep = Endpointer(CFG)
    evs = feed(ep, [0.0] * 5 + [0.9] * 10)
    start = next(e for e in evs if e.kind == "speech_start")
    assert start.segment.onset_at == pytest.approx(6 * F)
    assert start.t == pytest.approx((5 + frames_of(256)) * F)


def test_pre_roll_keeps_frames_before_onset_and_tail_after():
    ep = Endpointer(CFG)
    evs = feed(ep, [0.0] * 20 + [0.9] * 15 + [0.0] * 30)
    end = evs[-1]
    tags = [
        np.frombuffer(end.pcm[i : i + FRAME_BYTES], dtype=np.int16)[0]
        for i in range(0, len(end.pcm), FRAME_BYTES)
    ]
    pre = frames_of(320)
    tail = frames_of(160)
    assert tags[0] == 20 - pre  # the pre-roll starts `pre` frames before onset
    assert tags[-1] == 20 + 15 - 1 + tail  # last voiced + tail
    assert len(set(tags)) == len(tags)  # nothing captured twice


def test_short_blip_is_aborted_not_an_utterance():
    ep = Endpointer(CFG)
    evs = feed(ep, [0.0] * 5 + [0.9] * 4 + [0.0] * 30)  # 128 ms of "speech"
    assert [e.kind for e in evs] == ["onset", "abort"]
    assert not ep.in_speech


def test_pause_shorter_than_hangover_stays_one_utterance():
    ep = Endpointer(CFG)
    gap = frames_of(352)  # the pause inside the return_query clip
    evs = feed(ep, [0.9] * 20 + [0.0] * gap + [0.9] * 20 + [0.0] * 30)
    assert [e.kind for e in evs].count("speech_end") == 1


def test_hysteresis_keeps_weak_frames_inside_speech():
    ep = Endpointer(CFG)
    evs = feed(ep, [0.9] * 10 + [0.4] * 30 + [0.0] * 30)  # 0.4 < threshold but > neg_threshold
    end = next(e for e in evs if e.kind == "speech_end")
    assert end.segment.voiced_ms == pytest.approx(40 * FRAME_MS)


def test_max_utterance_forces_an_end():
    ep = Endpointer(EndpointerConfig(max_utterance_ms=1000))
    evs = feed(ep, [0.9] * 100)
    forced = [e for e in evs if e.kind == "speech_end"]
    assert forced and forced[0].forced


def test_frames_after_cut_become_pre_roll_for_next_segment():
    ep = Endpointer(CFG)
    evs = feed(ep, [0.9] * 10 + [0.0] * 20 + [0.9] * 10 + [0.0] * 20)
    ends = [e for e in evs if e.kind == "speech_end"]
    assert len(ends) == 2
    first = {
        np.frombuffer(ends[0].pcm[i : i + 2], dtype=np.int16)[0]
        for i in range(0, len(ends[0].pcm), FRAME_BYTES)
    }
    second = [
        np.frombuffer(ends[1].pcm[i : i + 2], dtype=np.int16)[0]
        for i in range(0, len(ends[1].pcm), FRAME_BYTES)
    ]
    assert 0 < second[0] < 30  # pre-roll came from the silence after the first
    assert not first & set(second)  # and no frame is in both utterances
    assert 30 in second


# ---- barge-in detector --------------------------------------------------------------------------

BI = BargeInConfig(duck_ms=160, confirm_ms=480, window_ms=1200, unduck_after_ms=320)


def run_detector(probs: list[float], cfg: BargeInConfig = BI) -> list[tuple[int, str]]:
    d = BargeInDetector(cfg)
    return [(i, a) for i, p in enumerate(probs) if (a := d.push(p))]


def test_duck_then_confirm_on_sustained_speech():
    acts = run_detector([0.9] * 30)
    assert acts == [(4, "duck"), (14, "confirm")]  # 5 frames = 160 ms, 15 frames = 480 ms


def test_short_speech_ducks_then_unducks():
    acts = run_detector([0.9] * 10 + [0.0] * 20)  # 320 ms "uh-huh"
    assert acts == [(4, "duck"), (19, "unduck")]


def test_noise_burst_shorter_than_duck_does_nothing():
    assert run_detector([0.9] * 3 + [0.0] * 30) == []


def test_gap_inside_interruption_does_not_reset_the_count():
    acts = run_detector([0.9] * 10 + [0.0] * 3 + [0.9] * 10)
    assert ("confirm" in [a for _, a in acts]) and acts[-1][0] == 10 + 3 + 4


def test_sparse_clicks_never_confirm():
    probs = ([0.9] * 2 + [0.0] * 8) * 30  # 64 ms click every 320 ms for ~10 s
    assert "confirm" not in [a for _, a in run_detector(probs)]


def test_false_start_and_real_interruption_are_judged_separately():
    probs = [0.9] * 12 + [0.0] * 15 + [0.9] * 20
    acts = run_detector(probs)
    # "uh": duck, silence: unduck (window cleared), real speech: duck again, then confirm
    assert [a for _, a in acts] == ["duck", "unduck", "duck", "confirm"]
    assert acts[-1][0] == 12 + 15 + 14


# ---- energy VAD ---------------------------------------------------------------------------------


def _frames(pcm: bytes) -> list[bytes]:
    return [pcm[i : i + FRAME_BYTES] for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES)]


def test_energy_vad_adapts_to_steady_noise_and_flags_speech():
    rng = np.random.default_rng(0)
    hum = (rng.normal(0, 150, FRAME_SAMPLES * 200)).astype(np.int16).tobytes()
    vad = EnergyVAD()
    probs = [vad.speech_prob(f) for f in _frames(hum)]
    assert max(probs[60:]) < 0.5  # once the floor has caught up, steady hiss is not speech
    speech = [vad.speech_prob(f) for f in _frames(tone(500, amp=0.3))]
    assert np.mean([p > 0.5 for p in speech]) >= 0.8  # syllable-rate dips may fall below


def test_energy_vad_silence_is_zero():
    vad = EnergyVAD()
    assert vad.speech_prob(b"\x00" * FRAME_BYTES) == 0.0


# ---- Silero on the clips (opt-in) ---------------------------------------------------------------

live = pytest.mark.skipif(os.environ.get("RUN_VOICE_LIVE") != "1", reason="RUN_VOICE_LIVE=1")


@live
@pytest.mark.parametrize(
    ("clip", "lo", "hi"),
    [
        ("noise_burst", 0, 0),
        ("keyboard_clicks", 0, 2),
        ("background_noise", 0, 3),
        ("false_start", 0, 6),
        ("backchannel", 8, 18),
        ("wait_wrong_order", 60, 200),
    ],
)
def test_silero_voiced_frames_on_clips(clip, lo, hi):
    from support_assistant.voice.vad import SileroVAD

    vad = SileroVAD()
    pcm = b"\x00" * FRAME_BYTES * 20 + read_wav(SYNTH / f"{clip}.wav") + b"\x00" * FRAME_BYTES * 20
    voiced = sum(vad.speech_prob(f) >= 0.5 for f in _frames(pcm))
    assert lo <= voiced <= hi
