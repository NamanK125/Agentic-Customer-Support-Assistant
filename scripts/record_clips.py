"""Record the real caller clips the voice scenarios use, into demo/clips/real/<name>.wav.

    uv run python scripts/record_clips.py                 # every clip in the manifest, in order
    uv run python scripts/record_clips.py wait_wrong_order backchannel
    uv run python scripts/record_clips.py --list          # what exists (real vs synthetic only)
    uv run python scripts/record_clips.py --play wait_wrong_order   # listen back
    uv run python scripts/record_clips.py --device 2 ...  # pick an input device (--devices lists)

For each clip: the line to say is shown, you press Enter, it records at 16 kHz mono and stops on
its own after you finish speaking (Silero-free energy endpointer, ~0.8 s of silence) or at the
max length. Noise clips ("noise" kind) record for a fixed duration instead. Leading and trailing
silence is trimmed to ~200 ms. Then you can keep it, redo it, or play it back.

Tips for the scenarios to mean something: record in a normal room, with the mic you'd call on;
say the backchannel ("Uh-huh.") the way you really would while listening, short and low; say the
false start ("Uh.") as a hesitation, not a word; for the interruption ("Wait, wrong order...")
start firmly, as if cutting someone off.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from support_assistant.voice.audio import read_wav, write_wav
from support_assistant.voice.contracts import FRAME_BYTES, FRAME_MS, FRAME_SAMPLES, SAMPLE_RATE
from support_assistant.voice.scenario import CLIPS, CLIPS_REAL, CLIPS_SYNTHETIC
from support_assistant.voice.vad import Endpointer, EndpointerConfig, EnergyVAD

NOISE_SECONDS = {"noise_burst": 2.0, "keyboard_clicks": 3.0, "background_noise": 6.0}


def _sd():
    import sounddevice as sd

    return sd


def record_speech(device: int | None, max_s: float, silence_ms: float) -> bytes:
    """Record until the speaker has finished (or max_s). Returns trimmed PCM16 mono 16 kHz."""
    sd = _sd()
    vad = EnergyVAD()
    ep = Endpointer(EndpointerConfig(hangover_ms=silence_ms, min_speech_ms=150, pre_roll_ms=300))
    frames: list[bytes] = []
    done = False
    with sd.RawInputStream(
        samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES, channels=1, dtype="int16", device=device
    ) as stream:
        n_max = int(max_s * 1000 / FRAME_MS)
        for i in range(n_max):
            data, _ = stream.read(FRAME_SAMPLES)
            frame = bytes(data)
            frames.append(frame)
            for ev in ep.push(frame, vad.speech_prob(frame), i * FRAME_MS / 1000):
                if ev.kind == "speech_start":
                    print("  ...hearing you", flush=True)
                if ev.kind == "speech_end":
                    done = True
            if done:
                break
    if not done:
        print(f"  (stopped at the {max_s:g} s limit)")
    return trim(b"".join(frames))


def record_fixed(device: int | None, seconds: float) -> bytes:
    sd = _sd()
    audio = sd.rec(
        int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=device
    )
    sd.wait()
    return audio.tobytes()


def trim(pcm: bytes, keep_ms: float = 200.0, margin_db: float = 15.0) -> bytes:
    """Cut leading/trailing silence, keeping `keep_ms` either side of the loud part."""
    x = np.frombuffer(pcm, dtype=np.int16)
    n = len(x) // FRAME_SAMPLES
    if n < 3:
        return pcm
    fr = x[: n * FRAME_SAMPLES].reshape(n, FRAME_SAMPLES).astype(np.float64)
    db = 20 * np.log10(np.sqrt((fr**2).mean(axis=1)) + 1e-3)
    loud = np.where(db > np.percentile(db, 10) + margin_db)[0]
    if not len(loud):
        return pcm
    keep = int(keep_ms / FRAME_MS)
    a, b = max(0, loud[0] - keep), min(n, loud[-1] + 1 + keep)
    return x[a * FRAME_SAMPLES : b * FRAME_SAMPLES].tobytes()


def play(pcm: bytes, device: int | None = None) -> None:
    sd = _sd()
    sd.play(np.frombuffer(pcm, dtype=np.int16), SAMPLE_RATE, device=device)
    sd.wait()


def describe(pcm: bytes) -> str:
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    peak = np.abs(x).max() / 32768 if len(x) else 0.0
    rms = np.sqrt((x**2).mean()) / 32768 if len(x) else 0.0
    warn = "  << very quiet, move closer" if peak < 0.05 else ""
    warn += "  << clipping, back off" if peak > 0.99 else ""
    level = 20 * np.log10(rms + 1e-9)
    return f"{len(x) / SAMPLE_RATE:.2f} s, peak {peak:.2f}, rms {level:.0f} dBFS{warn}"


def list_clips() -> None:
    for name, (kind, text) in CLIPS.items():
        real = CLIPS_REAL / f"{name}.wav"
        synth = CLIPS_SYNTHETIC / f"{name}.wav"
        state = "REAL" if real.exists() else ("synthetic only" if synth.exists() else "MISSING")
        extra = f" ({describe(read_wav(real))})" if real.exists() else ""
        print(f"{name:20s} {kind:5s} {state:15s} {text}{extra}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("clips", nargs="*", help="clip names (default: all in the manifest)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--devices", action="store_true", help="list audio devices")
    ap.add_argument("--play", action="store_true", help="play the named clips back, don't record")
    ap.add_argument("--device", type=int, default=None, help="input device index")
    ap.add_argument("--max-s", type=float, default=8.0)
    ap.add_argument("--silence-ms", type=float, default=800.0)
    ap.add_argument("--out", type=Path, default=CLIPS_REAL)
    args = ap.parse_args(argv)

    if args.devices:
        print(_sd().query_devices())
        return 0
    if args.list:
        list_clips()
        return 0
    names = args.clips or list(CLIPS)
    unknown = [n for n in names if n not in CLIPS]
    if unknown:
        sys.exit(f"unknown clip(s) {unknown}; manifest: {', '.join(CLIPS)}")
    if args.play:
        for n in names:
            p = args.out / f"{n}.wav"
            if not p.exists():
                print(f"{n}: not recorded yet")
                continue
            pcm = read_wav(p)
            print(f"{n}: {describe(pcm)}")
            play(pcm)
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    for n in names:
        kind, text = CLIPS[n]
        print(f"\n=== {n} ({kind}) ===")
        print(f"  {'SAY' if kind == 'say' else 'MAKE'}: {text}")
        while True:
            cmd = input("  Enter to record, s to skip, q to quit: ").strip().lower()
            if cmd == "q":
                return 0
            if cmd == "s":
                break
            if kind == "say":
                print("  recording... speak now")
                pcm = record_speech(args.device, args.max_s, args.silence_ms)
            else:
                secs = NOISE_SECONDS.get(n, 3.0)
                print(f"  recording {secs:g} s... go")
                pcm = record_fixed(args.device, secs)
            if len(pcm) < FRAME_BYTES * 3:
                print("  nothing usable recorded, try again")
                continue
            print(f"  got {describe(pcm)}")
            choice = input("  [k]eep, [p]lay, [r]edo? ").strip().lower() or "k"
            while choice == "p":
                play(pcm)
                choice = input("  [k]eep, [p]lay, [r]edo? ").strip().lower() or "k"
            if choice == "k":
                write_wav(args.out / f"{n}.wav", pcm)
                print(f"  saved {args.out / f'{n}.wav'}")
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
