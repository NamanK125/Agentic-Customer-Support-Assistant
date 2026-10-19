"""Voice activity: per-frame speech probability (Silero, or an energy fallback), the endpointer
that turns probabilities into utterances, and the barge-in detector used while the assistant talks.

Everything here is synchronous and pure (no I/O, no clock reads): the coordinator passes frame
timestamps in, which keeps the state machines deterministic and unit-testable.

Measured on the synthetic clips (Silero, threshold 0.5, 32 ms frames; clips regenerated
2026-10-19 12:38 without the in-house TTS's per-chunk WAV-header click), each clip alone after
silence (fresh model state):
  noise_burst (120 ms clap)   0 voiced frames      keyboard_clicks   0 voiced frames
  background_noise (hum)      0 voiced frames      false_start "Uh." (0.2 s)  0 voiced frames
  backchannel "Uh-huh."       13 voiced (~416 ms; the earlier render was 14-17, 450-545 ms)
  wait_wrong_order            "Wait," ~28 voiced, ~100 ms gap, then continuous speech
  return_query has a ~320 ms pause between its two sentences.
Silero is recurrent and never reset mid-call, so the same clip scores differently in a call:
after a previous utterance short vocalisations usually score lower, but over background hum
"Uh-huh." scored HIGH enough to confirm a barge-in at 480 ms (see BargeInConfig).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from support_assistant.voice.contracts import FRAME_BYTES, FRAME_MS, SAMPLE_RATE

# ---- Frame classifiers -----------------------------------------------------------------------


class SileroVAD:
    """Silero VAD v5 (silero-vad package, torch JIT), 512-sample frames at 16 kHz.

    Stateful across frames (RNN); `reset()` clears it. Per-frame inference is ~0.2 ms on an M-series
    CPU with one torch thread (measured), so it runs inline on the event loop; the coordinator
    records per-frame VAD time and reports the max so that claim stays checked.
    """

    def __init__(self, *, num_threads: int = 1) -> None:
        import torch
        from silero_vad import load_silero_vad

        torch.set_num_threads(num_threads)
        self._torch = torch
        self._model = load_silero_vad()
        self._model.eval()

    def speech_prob(self, frame: bytes) -> float:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"Silero needs {FRAME_BYTES}-byte frames, got {len(frame)}")
        x = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        with self._torch.no_grad():
            return float(self._model(self._torch.from_numpy(x), SAMPLE_RATE).item())

    def reset(self) -> None:
        self._model.reset_states()


class EnergyVAD:
    """Energy above an adaptive noise floor, squashed to a pseudo-probability.

    The floor is a low percentile (10th) of frame energy over the last ~2 s (minimum-statistics
    style): steady hum/hiss is absorbed within a few hundred ms, while speech, which dips between
    syllables, doesn't drag the floor up with it. Used by unit tests (deterministic, no torch) and
    as a documented fallback if torch is unavailable. Weakness, by design: it cannot tell a loud
    clap or a keyboard click from a syllable; only the duration gates downstream reject short
    transients, and a long stretch of loud non-speech (a TV) will read as speech.
    """

    def __init__(
        self,
        *,
        margin_db: float = 12.0,
        slope_db: float = 3.0,
        min_dbfs: float = -55.0,
        window_ms: float = 2000.0,
        percentile: float = 10.0,
    ) -> None:
        self._margin = margin_db
        self._slope = slope_db
        self._min = min_dbfs
        self._pct = percentile
        self._hist: deque[float] = deque(maxlen=max(4, int(window_ms / FRAME_MS)))
        self.floor_dbfs = min_dbfs

    def speech_prob(self, frame: bytes) -> float:
        x = np.frombuffer(frame, dtype=np.int16).astype(np.float64)
        rms = math.sqrt(float(np.mean(x * x))) if len(x) else 0.0
        db = 20 * math.log10(max(rms, 1e-3) / 32768.0)
        self._hist.append(db)
        self.floor_dbfs = float(np.percentile(self._hist, self._pct))
        if db < self._min:
            return 0.0
        # until there is some history, judge against the absolute minimum level
        floor = max(self.floor_dbfs, self._min) if len(self._hist) >= 8 else self._min
        z = (db - (floor + self._margin)) / self._slope
        return 1.0 / (1.0 + math.exp(-z))

    def reset(self) -> None:
        self._hist.clear()
        self.floor_dbfs = self._min


# ---- Endpointer ------------------------------------------------------------------------------


@dataclass(frozen=True)
class EndpointerConfig:
    threshold: float = 0.5  # prob to open a segment / count a voiced frame
    neg_threshold: float = 0.35  # hysteresis: inside a segment a frame stays voiced above this
    min_speech_ms: float = 250.0  # voiced ms before a segment counts as speech (rejects clicks)
    hangover_ms: float = 560.0  # trailing silence that ends an utterance (> the 350 ms pause seen)
    pre_roll_ms: float = 320.0  # audio kept from before onset so the first syllable isn't clipped
    tail_ms: float = 160.0  # silence kept after the last voiced frame (Whisper likes a margin)
    max_utterance_ms: float = 15_000.0


@dataclass
class Segment:
    """One stretch of possible speech, from the first voiced frame to `hangover_ms` of silence."""

    id: int
    onset_at: float  # end time of the first voiced frame
    frames: list[bytes] = field(default_factory=list)  # pre-roll + everything since onset
    voiced_ms: float = 0.0
    last_voiced_at: float = 0.0  # end time of the last voiced frame: "speech_end_at"
    last_voiced_idx: int = 0  # index into frames
    started_at: float | None = None  # when voiced_ms reached min_speech_ms
    # owned by the coordinator, kept here so they live and die with the segment
    overlapped_answer: bool = False
    barged_in: bool = False

    @property
    def started(self) -> bool:
        return self.started_at is not None


@dataclass(frozen=True)
class EndpointerEvent:
    kind: Literal["onset", "speech_start", "speech_end", "abort"]
    segment: Segment
    t: float  # time of the frame that produced the decision
    pcm: bytes = b""  # speech_end only: pre-roll + speech + tail
    forced: bool = False  # speech_end because max_utterance_ms was hit


class Endpointer:
    """Speech start/end state machine over (frame, prob, t) triples.

    - onset: first voiced frame after silence; a Segment opens with the pre-roll ring attached.
    - speech_start: the segment has accumulated `min_speech_ms` of voiced frames.
    - speech_end (EOU): `hangover_ms` of silence after a started segment. `segment.last_voiced_at`
      is the end of the last voiced frame, i.e. when the user actually stopped talking; the event
      time `t` is when we decided. Their difference is the hangover cost in the latency report.
    - abort: the segment closed before reaching min_speech_ms (a click, a cough, a noise burst).
    """

    def __init__(self, config: EndpointerConfig | None = None) -> None:
        self.cfg = config or EndpointerConfig()
        self._pre = deque(maxlen=max(0, int(round(self.cfg.pre_roll_ms / FRAME_MS))))
        self._seg: Segment | None = None
        self._next_id = 1
        self._silence_ms = 0.0

    @property
    def segment(self) -> Segment | None:
        return self._seg

    @property
    def in_speech(self) -> bool:
        """A segment is open and has passed min_speech_ms: the user is talking."""
        return self._seg is not None and self._seg.started

    def push(self, frame: bytes, prob: float, t: float) -> list[EndpointerEvent]:
        out: list[EndpointerEvent] = []
        seg = self._seg
        if seg is None:
            if prob < self.cfg.threshold:
                self._pre.append(frame)
                return out
            seg = Segment(id=self._next_id, onset_at=t, frames=[*self._pre, frame])
            self._next_id += 1
            self._pre.clear()
            self._seg = seg
            self._silence_ms = 0.0
            self._voiced(seg, t)
            out.append(EndpointerEvent("onset", seg, t))
        else:
            seg.frames.append(frame)
            if prob >= self.cfg.neg_threshold:
                self._voiced(seg, t)
            else:
                self._silence_ms += FRAME_MS
        if not seg.started and seg.voiced_ms >= self.cfg.min_speech_ms:
            seg.started_at = t
            out.append(EndpointerEvent("speech_start", seg, t))
        if self._silence_ms >= self.cfg.hangover_ms:
            out.append(self._close(seg, t, forced=False))
        elif seg.started and len(seg.frames) * FRAME_MS >= self.cfg.max_utterance_ms:
            out.append(self._close(seg, t, forced=True))
        return out

    def _voiced(self, seg: Segment, t: float) -> None:
        seg.voiced_ms += FRAME_MS
        seg.last_voiced_at = t
        seg.last_voiced_idx = len(seg.frames) - 1
        self._silence_ms = 0.0

    def _close(self, seg: Segment, t: float, *, forced: bool) -> EndpointerEvent:
        self._seg = None
        self._silence_ms = 0.0
        if not seg.started:
            return EndpointerEvent("abort", seg, t)
        tail = int(round(self.cfg.tail_ms / FRAME_MS))
        pcm = b"".join(seg.frames[: seg.last_voiced_idx + 1 + tail])
        # frames after the cut become pre-roll for whatever comes next
        for f in seg.frames[seg.last_voiced_idx + 1 :]:
            self._pre.append(f)
        return EndpointerEvent("speech_end", seg, t, pcm=pcm, forced=forced)

    def reset(self) -> None:
        self._seg = None
        self._pre.clear()
        self._silence_ms = 0.0


# ---- Barge-in detector -----------------------------------------------------------------------


@dataclass(frozen=True)
class BargeInConfig:
    """Two-stage barge-in thresholds, tuned on the clips (see decisions log for the sweeps).

    duck_ms 160: voiced ms in the window before playback is ducked (cheap and reversible). Five
      frames: a 120 ms clap never gets there under Silero; any real word does.
    confirm_ms 560: voiced ms in the window before playback is cut and the speech becomes a turn.
      Started at 480 (the usual ~450-500 ms). In-call sweep on the regenerated clips (11 placements
      into the answer, with and without background hum): "Uh-huh." over hum confirmed a barge-in
      11/11 at 480 and 0/11 at 560 and 640; without hum 0/11 everywhere; "Uh." and the noise clips
      never ducked or confirmed; "Wait, wrong order" confirmed 22/22 at all three, inject->confirm
      p50 ~688 / 784 / 848 ms. So 560 removes the false interruption for ~96 ms more time-to-cut;
      the duck at 160 ms is what the caller notices first anyway.
      The margin is thin (one clip, one voice): a drawn-out "mmm-hmm" over noise will cut in.
    window_ms: voiced frames are counted in a sliding window, so a short gap inside a real
      interruption ("Wait, [100 ms] wrong order") doesn't reset the count, but sparse transients
      (keyboard clicks every 300 ms) cannot add up over many seconds.
    unduck_after_ms: that much silence while ducked means "it was nothing": restore volume and
      clear the window, so a false start and a later real interruption are judged separately.
    """

    threshold: float = 0.5
    duck_ms: float = 160.0
    confirm_ms: float = 560.0
    window_ms: float = 1200.0
    unduck_after_ms: float = 320.0
    duck_gain: float = 0.3


BargeAction = Literal["duck", "confirm", "unduck"]


class BargeInDetector:
    """Fed one probability per frame while the assistant is audible. Returns an action when the
    stage changes. After "confirm" it stays inert until `reset()`."""

    def __init__(self, config: BargeInConfig | None = None) -> None:
        self.cfg = config or BargeInConfig()
        n = max(1, int(round(self.cfg.window_ms / FRAME_MS)))
        self._win: deque[bool] = deque(maxlen=n)
        self._stage: Literal["idle", "ducked", "confirmed"] = "idle"
        self._silence_ms = 0.0

    @property
    def stage(self) -> str:
        return self._stage

    @property
    def voiced_ms(self) -> float:
        return sum(self._win) * FRAME_MS

    def push(self, prob: float) -> BargeAction | None:
        if self._stage == "confirmed":
            return None
        voiced = prob >= self.cfg.threshold
        self._win.append(voiced)
        self._silence_ms = 0.0 if voiced else self._silence_ms + FRAME_MS
        v = self.voiced_ms
        if self._stage == "idle":
            if v >= self.cfg.confirm_ms:  # only reachable with duck_ms >= confirm_ms
                self._stage = "confirmed"
                return "confirm"
            if v >= self.cfg.duck_ms:
                self._stage = "ducked"
                return "duck"
            if self._silence_ms >= self.cfg.unduck_after_ms:
                self._win.clear()  # forget isolated blips
            return None
        # ducked
        if v >= self.cfg.confirm_ms:
            self._stage = "confirmed"
            return "confirm"
        if self._silence_ms >= self.cfg.unduck_after_ms:
            self._stage = "idle"
            self._win.clear()
            return "unduck"
        return None

    def reset(self) -> None:
        self._win.clear()
        self._stage = "idle"
        self._silence_ms = 0.0
