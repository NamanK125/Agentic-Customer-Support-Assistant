"""Audio I/O for the phone line: scripted clip injection and a recording sink (scenario mode), and
mic/speaker via sounddevice (live mode).

Echo. Neither mode does acoustic echo cancellation:
- Scenario mode is echo-free by construction: `ClipInjectionSource` only ever emits room tone plus
  the clips the runner injects. The assistant's TTS goes to `RecordingSink` and never reaches the
  source, so the VAD hears exactly what a caller on a headset would send, nothing else.
- Live mode assumes headphones. With open speakers the mic hears the assistant, Silero rightly
  calls that speech, and the assistant barges in on itself. AEC is out of scope (documented).

Timing. Sources are paced against the event loop's monotonic clock with absolute deadlines
(frame k is due at t0 + k * 32 ms), so a late wake-up is caught up on the next frame instead of
accumulating drift. Sinks are paced like a real device: `write` returns about when the audio has
been handed to the speaker, so the time of the first `write` is when the caller starts hearing it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import wave
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from support_assistant.voice.contracts import (
    FRAME_BYTES,
    FRAME_MS,
    FRAME_SAMPLES,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    pcm_ms,
)

log = logging.getLogger(__name__)

FRAME_S = FRAME_MS / 1000


def read_wav(path: Path) -> bytes:
    """Read a mono PCM16 16 kHz WAV. Anything else is rejected rather than silently resampled,
    since a wrong rate would make every VAD/latency number meaningless."""
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, SAMPLE_WIDTH, SAMPLE_RATE):
            raise ValueError(
                f"{path}: need mono PCM16 {SAMPLE_RATE} Hz, got {w.getnchannels()} ch "
                f"{w.getsampwidth() * 8}-bit {w.getframerate()} Hz"
            )
        return w.readframes(w.getnframes())


def write_wav(path: Path, pcm: bytes, *, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)


def _i16(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype=np.int16)


def _now() -> float:
    return asyncio.get_running_loop().time()


# ---- Scenario-mode source ------------------------------------------------------------------


@dataclass
class Injection:
    label: str
    at_s: float  # loop time of the first frame that contains it
    duration_ms: float


class ClipInjectionSource:
    """A continuous, real-time paced caller line: room tone, with clips mixed in on demand.

    `inject(pcm)` may be called at any time (e.g. "1.2 s after the assistant starts speaking");
    the clip starts on the next frame and overlapping clips are summed. The assistant's TTS output
    never enters this source, so scenario runs have no echo path (see module docstring).

    Everything emitted is recorded (`recorded()`) for the left channel of the call WAV.
    """

    def __init__(
        self,
        *,
        room_tone_rms: float = 30.0,
        background: bytes | None = None,
        seed: int = 0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._rng = np.random.default_rng(seed)
        self._tone_rms = room_tone_rms
        self._background = _i16(background).astype(np.int32) if background else None
        self._bg_pos = 0
        self._active: list[tuple[np.ndarray, int]] = []  # (samples, position)
        self._clock = clock or _now
        self._record: list[bytes] = []
        self.injections: list[Injection] = []
        self.started_at: float | None = None

    def inject(self, pcm: bytes, label: str = "clip") -> Injection:
        samples = _i16(pcm).astype(np.int32)
        self._active.append((samples, 0))
        inj = Injection(label, self._clock(), pcm_ms(pcm))
        self.injections.append(inj)
        return inj

    @property
    def busy(self) -> bool:
        """True while an injected clip is still being emitted."""
        return bool(self._active)

    def _next_frame(self) -> bytes:
        if self._tone_rms > 0:
            mix = self._rng.normal(0.0, self._tone_rms, FRAME_SAMPLES).astype(np.int32)
        else:
            mix = np.zeros(FRAME_SAMPLES, dtype=np.int32)
        if self._background is not None and len(self._background):
            idx = (self._bg_pos + np.arange(FRAME_SAMPLES)) % len(self._background)
            mix += self._background[idx]
            self._bg_pos = (self._bg_pos + FRAME_SAMPLES) % len(self._background)
        still: list[tuple[np.ndarray, int]] = []
        for samples, pos in self._active:
            part = samples[pos : pos + FRAME_SAMPLES]
            mix[: len(part)] += part
            if pos + FRAME_SAMPLES < len(samples):
                still.append((samples, pos + FRAME_SAMPLES))
        self._active = still
        return np.clip(mix, -32768, 32767).astype(np.int16).tobytes()

    async def frames(self) -> AsyncIterator[bytes]:
        t0 = self._clock()
        self.started_at = t0
        k = 0
        while True:
            # absolute deadline: frame k is "captured" at t0 + (k+1) * 32 ms
            delay = t0 + (k + 1) * FRAME_S - self._clock()
            if delay > 0:
                await asyncio.sleep(delay)
            frame = self._next_frame()
            self._record.append(frame)
            k += 1
            yield frame

    def recorded(self) -> bytes:
        return b"".join(self._record)


# ---- Scenario-mode sink ----------------------------------------------------------------------


@dataclass
class _Played:
    at_s: float
    pcm: bytes  # already gain-applied


class RecordingSink:
    """A speaker stand-in that is paced like one and records what the caller actually heard.

    `write` plays frame by frame in real time (each 32 ms slice is released at its due time with
    the gain current at that moment), so ducking and a barge-in `stop()` land in the recording
    exactly when they happened. `stop()` makes any in-progress `write` return at once and drops
    the rest of its audio, as a speaker buffer flush would.
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self.clock = clock
        self._played: list[_Played] = []
        self._gain = 1.0
        self._gen = 0
        self._busy_until = 0.0
        self.gain_changes: list[tuple[float, float]] = []
        self.stops: list[float] = []

    @property
    def gain(self) -> float:
        return self._gain

    def heard_ms(self) -> float:
        return sum(pcm_ms(p.pcm) for p in self._played)

    def _t(self) -> float:
        return (self.clock or _now)()

    async def write(self, pcm: bytes) -> None:
        gen = self._gen
        start = max(self._t(), self._busy_until)
        samples = _i16(pcm)
        for k in range(0, len(samples), FRAME_SAMPLES):
            if gen != self._gen:
                return  # stopped
            due = start + k / SAMPLE_RATE
            delay = due - self._t()
            if delay > 0:
                await asyncio.sleep(delay)
                if gen != self._gen:
                    return
            part = samples[k : k + FRAME_SAMPLES]
            if self._gain != 1.0:
                part = (part.astype(np.float32) * self._gain).astype(np.int16)
            self._played.append(_Played(due, part.tobytes()))
            self._busy_until = due + len(part) / SAMPLE_RATE
        # hold until the last slice has finished playing, as a device with a tiny buffer would
        delay = self._busy_until - self._t()
        if delay > 0 and gen == self._gen:
            await asyncio.sleep(delay)

    def set_gain(self, gain: float) -> None:
        g = min(1.0, max(0.0, gain))
        if g != self._gain:
            self._gain = g
            self.gain_changes.append((self._t(), g))

    async def stop(self) -> None:
        now = self._t()
        self._gen += 1
        self.stops.append(now)
        # truncate a slice that was "on the speaker" at stop time
        if self._played and self._played[-1].at_s + pcm_ms(self._played[-1].pcm) / 1000 > now:
            last = self._played[-1]
            keep = max(0, int((now - last.at_s) * SAMPLE_RATE)) * SAMPLE_WIDTH
            self._played[-1] = _Played(last.at_s, last.pcm[:keep])
        self._busy_until = now

    def render(self, t0: float, duration_s: float) -> bytes:
        """Mono PCM of everything heard, placed on a timeline starting at `t0`."""
        n = int(duration_s * SAMPLE_RATE)
        out = np.zeros(n, dtype=np.int32)
        for p in self._played:
            i = int(round((p.at_s - t0) * SAMPLE_RATE))
            s = _i16(p.pcm)
            if i >= n or i + len(s) <= 0:
                continue
            lo = max(0, -i)
            hi = min(len(s), n - i)
            out[i + lo : i + hi] += s[lo:hi]
        return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


def stereo_call(caller: bytes, assistant: bytes) -> bytes:
    """Interleave caller (left) and assistant (right) into one stereo PCM16 buffer."""
    a, b = _i16(caller), _i16(assistant)
    n = max(len(a), len(b))
    out = np.zeros((n, 2), dtype=np.int16)
    out[: len(a), 0] = a
    out[: len(b), 1] = b
    return out.tobytes()


# ---- Live mode (sounddevice) ----------------------------------------------------------------


class MicSource:
    """Microphone frames at 16 kHz mono via a sounddevice InputStream. The PortAudio callback runs
    on its own thread and hands frames to the loop with `call_soon_threadsafe`; nothing blocks the
    event loop. Assumes headphones: there is no echo cancellation (see module docstring)."""

    def __init__(self, device: int | str | None = None, *, max_queue: int = 500) -> None:
        self._device = device
        self._max_queue = max_queue
        self.overflows = 0
        self.dropped = 0

    async def frames(self) -> AsyncIterator[bytes]:
        import sounddevice as sd

        loop = asyncio.get_running_loop()
        q: asyncio.Queue[bytes] = asyncio.Queue()

        def put(frame: bytes) -> None:
            if q.qsize() >= self._max_queue:  # consumer stalled: drop rather than grow forever
                self.dropped += 1
                return
            q.put_nowait(frame)

        def callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
            if status.input_overflow:
                self.overflows += 1
            loop.call_soon_threadsafe(put, bytes(indata))

        stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=FRAME_SAMPLES,
            channels=1,
            dtype="int16",
            device=self._device,
            callback=callback,
        )
        stream.start()
        try:
            while True:
                frame = await q.get()
                if len(frame) == FRAME_BYTES:
                    yield frame
        finally:
            stream.stop()
            stream.close()


class SpeakerSink:
    """Speaker output via a sounddevice OutputStream fed from a ring buffer.

    `write` appends to the buffer and returns once the buffer is below `lead_ms` of audio, so
    writes are paced in real time and `stop()` has at most `lead_ms` + device latency to flush.
    The callback (PortAudio thread) applies the current gain per block. `stop()` clears the
    buffer immediately: that is the barge-in cut.
    """

    def __init__(self, device: int | str | None = None, *, lead_ms: float = 120.0) -> None:
        self._device = device
        self._lead = int(lead_ms / 1000 * SAMPLE_RATE) * SAMPLE_WIDTH
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._gain = 1.0
        self._gen = 0
        self._stream = None

    def _ensure(self) -> None:
        if self._stream is not None:
            return
        import sounddevice as sd

        def callback(outdata, frames, time_info, status) -> None:  # noqa: ANN001
            need = frames * SAMPLE_WIDTH
            with self._lock:
                chunk = bytes(self._buf[:need])
                del self._buf[:need]
                gain = self._gain
            if gain != 1.0 and chunk:
                chunk = (_i16(chunk).astype(np.float32) * gain).astype(np.int16).tobytes()
            outdata[: len(chunk)] = chunk
            outdata[len(chunk) :] = b"\x00" * (need - len(chunk))

        self._stream = sd.RawOutputStream(
            samplerate=SAMPLE_RATE,
            blocksize=FRAME_SAMPLES,
            channels=1,
            dtype="int16",
            device=self._device,
            callback=callback,
        )
        self._stream.start()

    def _buffered(self) -> int:
        with self._lock:
            return len(self._buf)

    async def write(self, pcm: bytes) -> None:
        self._ensure()
        gen = self._gen
        step = FRAME_BYTES * 4
        for i in range(0, len(pcm), step):
            while self._buffered() > self._lead:
                if gen != self._gen:
                    return
                await asyncio.sleep(FRAME_S / 2)
            if gen != self._gen:
                return
            with self._lock:
                self._buf += pcm[i : i + step]
        # hold until handed to the device; the buffer drains on the PortAudio thread, so poll
        while self._buffered() > 0 and gen == self._gen:  # noqa: ASYNC110
            await asyncio.sleep(FRAME_S / 2)

    def set_gain(self, gain: float) -> None:
        with self._lock:
            self._gain = min(1.0, max(0.0, gain))

    async def stop(self) -> None:
        self._gen += 1
        with self._lock:
            self._buf.clear()

    def close(self) -> None:
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
                self._stream.close()
            self._stream = None
