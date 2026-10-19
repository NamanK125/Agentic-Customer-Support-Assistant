"""Scenario runner: every demo scenario runs offline (virtual time, fake leaf providers inside the
real fallback chains and fault wrappers, scripted responder) and must meet its own expectations.
With RUN_VOICE_LIVE=1 the same runs use Silero instead of the energy VAD."""

from __future__ import annotations

import json
import os
import wave

import pytest

from support_assistant.voice.audio import write_wav
from support_assistant.voice.scenario import (
    SCENARIOS_DIR,
    RunOptions,
    check_expectations,
    resolve_clip,
    run_scenario,
    run_virtual,
    tone,
)

SCENARIOS = sorted(SCENARIOS_DIR.glob("*.json"))
VADS = ["energy"] + (["silero"] if os.environ.get("RUN_VOICE_LIVE") == "1" else [])


def test_all_required_scenarios_exist():
    names = {p.stem for p in SCENARIOS}
    for n in [
        "01_silence_full_playback",
        "02_noise_burst_no_interrupt",
        "03_real_interruption",
        "04_false_start_then_interrupt",
        "05_backchannel",
        "06_overlap_at_playback_start",
        "07_tts_failure_mid_call",
        "08_stt_invalid_key",
        "09_everything_down",
        "10_latency_run",
        "11_keyboard_noise_during_reply",
    ]:
        assert n in names
    for p in SCENARIOS:
        spec = json.loads(p.read_text())
        assert spec["name"] == p.stem
        assert spec["responder"] in ("scripted", "pipeline")
        assert spec["responder"] in spec["description"] or "scripted" in spec["description"]
        assert spec["expect"].get("no_duplicate_turns") is True


@pytest.mark.parametrize("vad", VADS)
@pytest.mark.parametrize("path", SCENARIOS, ids=lambda p: p.stem)
def test_scenario_offline_meets_its_expectations(path, vad):
    spec = json.loads(path.read_text())
    opts = RunOptions(providers="fake", vad=vad, responder="scripted", out_root=None)
    r = run_virtual(run_scenario(spec, opts))
    failed = [e for e in r.expectations if not e["pass"]]
    assert not r.metrics["timed_out"]
    assert not r.metrics["runner_error"]
    assert r.metrics["no_duplicate_turns"] and r.metrics["no_dropped_turns"]
    assert r.metrics["coordinator_errors"] == 0 and r.metrics["invariant_violations"] == 0
    assert not failed, failed


def test_run_writes_all_outputs(tmp_path):
    spec = json.loads((SCENARIOS_DIR / "03_real_interruption.json").read_text())
    opts = RunOptions(providers="fake", vad="energy", responder="scripted", out_root=tmp_path)
    r = run_virtual(run_scenario(spec, opts))
    assert r.passed and r.run_dir is not None
    for f in ["events.jsonl", "transcript.md", "call.wav", "latency.json", "result.json"]:
        assert (r.run_dir / f).exists(), f
    with wave.open(str(r.run_dir / "call.wav")) as w:
        assert w.getnchannels() == 2 and w.getframerate() == 16000
        assert w.getnframes() / 16000 == pytest.approx(r.metrics["call_s"], abs=0.5)
    transcript = (r.run_dir / "transcript.md").read_text()
    assert "BARGE-IN confirmed" in transcript and "synthetic" in transcript.lower()
    result = json.loads((r.run_dir / "result.json").read_text())
    assert result["responder"] == "scripted"
    assert result["clips"]["wait_wrong_order"]["real"] is False
    lat = json.loads((r.run_dir / "latency.json").read_text())
    assert lat["summary"]["to_first_answer_audio"]["n"] == 2
    lines = (r.run_dir / "events.jsonl").read_text().splitlines()
    ts = [json.loads(x)["t_ms"] for x in lines]
    assert ts == sorted(ts)  # one clock for coordinator, providers and runner


def test_real_clip_takes_precedence(tmp_path):
    real, synth = tmp_path / "real", tmp_path / "synth"
    write_wav(synth / "x.wav", tone(300))
    c = resolve_clip("x", real_dir=real, synth_dir=synth)
    assert not c.real
    write_wav(real / "x.wav", tone(500))
    c = resolve_clip("x", real_dir=real, synth_dir=synth)
    assert c.real and c.ms == pytest.approx(500, abs=1)
    with pytest.raises(FileNotFoundError):
        resolve_clip("missing", real_dir=real, synth_dir=synth)


def test_expectation_operators():
    m = {"a": 2, "b": ["edge", "inhouse"], "c": True, "d": ["tts_failed"]}
    res = check_expectations(
        {"a": {">=": 2, "<": 3}, "b": "edge", "c": True, "d": {"contains": "tts_failed"}}, m
    )
    assert all(r["pass"] for r in res)
    res = check_expectations({"a": 3, "b": "groq", "missing": 1}, m)
    assert not any(r["pass"] for r in res)
