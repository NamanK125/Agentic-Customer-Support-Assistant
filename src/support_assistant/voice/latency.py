"""Per-turn latency bookkeeping: instrumented timestamps, not estimates.

All marks are seconds on the coordinator's clock (the event loop's monotonic clock). The start
point is `speech_end_at`, the end of the caller's last voiced frame, which is when a human would
say they stopped talking. Two totals are reported because they answer different questions:

- end-of-speech -> first audio: when the caller first hears *anything* (the "one moment" filler
  counts). This is what decides whether the line feels dead.
- end-of-speech -> first answer audio: when the caller hears the actual answer. With an LLM that
  decodes ~10 tok/s this is several seconds, and the filler exists to cover exactly that gap.

Stage attribution (each is the time spent waiting on that stage, in turn order):
  hangover = eou_at - speech_end_at          (endpointer waiting for silence; a tuning knob)
  stt      = stt_done - eou_at
  llm      = reply_ready - stt_done          (the whole text pipeline: guards, router, agent)
  tts      = tts_first_chunk - reply_ready   (time to first synthesized chunk)
  playout  = first_answer_audio_played - tts_first_chunk (sink hand-off; ~0 unless filler holds it)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

MARKS = (
    "speech_end_at",
    "eou_at",
    "stt_done",
    "reply_ready",
    "tts_first_chunk",
    "first_audio_played",
    "first_answer_audio_played",
)

STAGES = (
    ("hangover", "speech_end_at", "eou_at"),
    ("stt", "eou_at", "stt_done"),
    ("llm", "stt_done", "reply_ready"),
    ("tts", "reply_ready", "tts_first_chunk"),
    ("playout", "tts_first_chunk", "first_answer_audio_played"),
)


@dataclass
class TurnLatency:
    turn: int
    speech_end_at: float | None = None
    eou_at: float | None = None
    stt_done: float | None = None
    reply_ready: float | None = None
    tts_first_chunk: float | None = None
    first_audio_played: float | None = None
    first_answer_audio_played: float | None = None
    first_audio_kind: str | None = None  # "filler" | "answer" | "canned"
    excluded: str | None = None  # why this turn is not in the stats (merged, failed, ...)

    def ms(self, a: str, b: str) -> float | None:
        x, y = getattr(self, a), getattr(self, b)
        return None if x is None or y is None else round((y - x) * 1000, 1)

    def breakdown(self) -> dict[str, float | None]:
        out: dict[str, float | None] = {name: self.ms(a, b) for name, a, b in STAGES}
        out["to_first_audio"] = self.ms("speech_end_at", "first_audio_played")
        out["to_first_answer_audio"] = self.ms("speech_end_at", "first_answer_audio_played")
        return out

    def explain(self) -> str:
        b = self.breakdown()
        total = b["to_first_answer_audio"]
        if total is None:
            return "no answer audio (see turn outcome)"
        parts = [f"{name} {b[name]:.0f}" for name, _, _ in STAGES if b[name] is not None]
        biggest = max(
            ((name, b[name]) for name, _, _ in STAGES if b[name] is not None),
            key=lambda kv: kv[1] or 0.0,
        )
        first = b["to_first_audio"]
        lead = (
            f"first audio {first:.0f} ms ({self.first_audio_kind}); " if first is not None else ""
        )
        return (
            f"{lead}answer audio {total:.0f} ms = " + " + ".join(parts) + f" ms; "
            f"dominated by {biggest[0]} ({(biggest[1] or 0) / total:.0%})"
        )


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (no interpolation: with 10 turns, p95 is the slowest turn)."""
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(-(-p * len(s) // 100)) - 1))
    return s[k]


class LatencyTracker:
    def __init__(self) -> None:
        self.turns: dict[int, TurnLatency] = {}

    def turn(self, turn: int) -> TurnLatency:
        return self.turns.setdefault(turn, TurnLatency(turn))

    def mark(self, turn: int, name: str, t: float, *, overwrite: bool = False) -> None:
        if name not in MARKS:
            raise ValueError(f"unknown latency mark {name!r}")
        tl = self.turn(turn)
        if overwrite or getattr(tl, name) is None:
            setattr(tl, name, t)

    def exclude(self, turn: int, reason: str) -> None:
        self.turn(turn).excluded = reason

    def report(self, t0: float = 0.0) -> dict:
        rows = []
        for tl in sorted(self.turns.values(), key=lambda x: x.turn):
            row = {
                f.name: (round((v - t0) * 1000, 1) if isinstance(v, float) else v)
                for f, v in ((f, getattr(tl, f.name)) for f in fields(tl))
            }
            row["breakdown_ms"] = tl.breakdown()
            row["explain"] = tl.explain()
            rows.append(row)
        counted = [tl for tl in self.turns.values() if tl.excluded is None]

        def stat(key: str) -> dict[str, float | int | None]:
            vals = [v for tl in counted if (v := tl.breakdown()[key]) is not None]
            return {"n": len(vals), "p50": percentile(vals, 50), "p95": percentile(vals, 95)}

        summary = {k: stat(k) for k in ("to_first_audio", "to_first_answer_audio")}
        summary["stages"] = {name: stat(name) for name, _, _ in STAGES}
        return {
            "definition": {
                "start": "speech_end_at = end of the caller's last voiced 32 ms frame",
                "to_first_audio": "first sink write of any assistant audio (filler included)",
                "to_first_answer_audio": "first sink write of the answer itself",
                "percentiles": "nearest-rank",
            },
            "summary": summary,
            "turns": rows,
        }

    def as_dicts(self) -> list[dict]:
        return [asdict(t) for t in self.turns.values()]
