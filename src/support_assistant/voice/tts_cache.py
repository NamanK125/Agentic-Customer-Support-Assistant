"""Sentence-level, frequency-based TTS audio cache, keyed by (speaker, sentence).

The coordinator speaks replies one sentence at a time, so recurring sentences ("How can I help
you today?", the greeting, the refund-inspection policy line) are synthesized once per speaker and
then played with no synthesis round trip.

Rules that keep it correct:
- The key includes the speaker id, so a call never hears cached audio in another voice.
- Only audio fully produced by the primary provider in the pinned voice is stored. A stream that
  was cancelled (barge-in), failed, or fell back to another provider (different voice, and possibly
  a restarted sentence) is never cached.
- Sentences containing digits (order ids, dates, amounts) are kept in memory only, never written
  to disk, so customer-specific details don't persist. Everything else is persisted under
  `.cache/voice/tts/` and survives restarts.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from support_assistant.observability import meter
from support_assistant.voice.contracts import FRAME_BYTES, TTS, CallEvent, EventSink

_meter = meter(__name__)
_hits = _meter.create_counter("voice.tts_cache.hits")
_misses = _meter.create_counter("voice.tts_cache.misses")

_DEFAULT_DIR = Path(__file__).resolve().parents[3] / ".cache" / "voice" / "tts"
_CHUNK = FRAME_BYTES * 6  # ~192 ms per yielded chunk on a hit


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class TTSAudioCache:
    """Process-wide, frequency-based store shared by all calls.

    Admission: a sentence is stored only after it has been requested `admit_after` times (per
    speaker), so one-off sentences ("your order PN-10002 ships Tuesday") never take space, while
    recurring ones are synthesized at most `admit_after` times and then served from cache.
    Eviction: least-frequently-used first (ties: least recently used). Counts are halved every
    `decay_every` requests so yesterday's popular sentence can age out. Pinned entries
    (prewarmed greeting and fixed phrases) are never evicted.
    """

    def __init__(
        self,
        *,
        max_bytes: int = 64 * 1024 * 1024,
        admit_after: int = 2,
        decay_every: int = 5000,
        disk_dir: Path | None = _DEFAULT_DIR,
    ) -> None:
        self._mem: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._freq: dict[tuple[str, str], int] = {}
        self._pinned: set[tuple[str, str]] = set()
        self._bytes = 0
        self._max = max_bytes
        self._admit_after = admit_after
        self._decay_every = decay_every
        self._requests = 0
        self._dir = disk_dir
        self.hits = 0
        self.misses = 0

    def _path(self, speaker: str, text: str) -> Path | None:
        if self._dir is None:
            return None
        h = hashlib.sha256(f"{speaker}\x00{text}".encode()).hexdigest()[:24]
        return self._dir / f"{h}.pcm"

    def frequency(self, speaker: str, text: str) -> int:
        return self._freq.get((speaker, _norm(text)), 0)

    async def get(self, speaker: str, text: str) -> bytes | None:
        """Look up a sentence and count the request (this is what drives admission)."""
        key = (speaker, _norm(text))
        self._count(key)
        if (pcm := self._mem.get(key)) is not None:
            self._mem.move_to_end(key)
            return pcm
        path = self._path(*key)
        if path is not None and await asyncio.to_thread(path.exists):
            # On disk = it was frequent before a restart; bring it back into memory.
            pcm = await asyncio.to_thread(path.read_bytes)
            self._store(key, pcm)
            return pcm
        return None

    async def offer(self, speaker: str, text: str, pcm: bytes, *, pin: bool = False) -> bool:
        """Store a clean render if the sentence is frequent enough (or pinned). Returns whether
        it was stored."""
        key = (speaker, _norm(text))
        if not pcm or not (pin or self._freq.get(key, 0) >= self._admit_after):
            return False
        if pin:
            self._pinned.add(key)
        self._store(key, pcm)
        path = self._path(*key)
        if path is not None and not re.search(r"\d", key[1]):  # no ids/dates/amounts on disk
            await asyncio.to_thread(self._write, path, pcm)
        return True

    def _count(self, key: tuple[str, str]) -> None:
        self._freq[key] = self._freq.get(key, 0) + 1
        self._requests += 1
        if self._requests % self._decay_every == 0:  # age counts so popularity can fade
            self._freq = {k: v // 2 for k, v in self._freq.items() if v // 2 or k in self._mem}

    @staticmethod
    def _write(path: Path, pcm: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(pcm)
        tmp.replace(path)  # atomic: a reader never sees half a file

    def _store(self, key: tuple[str, str], pcm: bytes) -> None:
        if key in self._mem:
            self._bytes -= len(self._mem.pop(key))
        self._mem[key] = pcm
        self._bytes += len(pcm)
        while self._bytes > self._max:
            victims = [k for k in self._mem if k not in self._pinned and k != key]
            if not victims:
                break
            # LFU; OrderedDict order breaks ties toward least recently used.
            victim = min(victims, key=lambda k: self._freq.get(k, 0))
            self._bytes -= len(self._mem.pop(victim))

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        top = sorted(self._freq.items(), key=lambda kv: -kv[1])[:5]
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else None,
            "entries": len(self._mem),
            "pinned": len(self._pinned),
            "bytes": self._bytes,
            "admit_after": self._admit_after,
            "top_sentences": [{"text": k[1][:60], "requests": v} for k, v in top],
        }


class CachingTTS:
    """Wraps a call's TTS chain: serves cached sentences instantly, caches clean primary renders.

    Every other attribute (`last_provider`, `breaker`, ...) is forwarded to the wrapped chain.
    """

    name = "cached-tts"

    def __init__(
        self,
        inner: TTS,
        cache: TTSAudioCache,
        *,
        speaker: str,
        primary: str,
        events: EventSink | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._inner = inner
        self._cache = cache
        self.speaker = speaker
        self._primary = primary
        self._events = events
        self._clock = clock

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)

    def _emit(self, kind: str, **data: Any) -> None:
        if self._events is not None:
            t = self._clock() if self._clock else 0.0
            self._events(CallEvent(t_ms=t, kind=kind, data=data))

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        cached = await self._cache.get(self.speaker, text)
        if cached is not None:
            self._cache.hits += 1
            _hits.add(1)
            self._emit("tts_cache_hit", text=text[:80], audio_ms=round(len(cached) / 32, 1))
            for i in range(0, len(cached), _CHUNK):
                yield cached[i : i + _CHUNK]
            return

        self._cache.misses += 1
        _misses.add(1)
        parts: list[bytes] = []
        completed = False
        try:
            async for chunk in self._inner.stream(text):
                parts.append(chunk)
                yield chunk
            completed = True
        finally:
            served = getattr(self._inner, "last_provider", None)
            if completed and served == self._primary:
                if await self._cache.offer(self.speaker, text, b"".join(parts)):
                    self._emit("tts_cache_store", text=text[:80],
                               requests=self._cache.frequency(self.speaker, text))
            elif completed:
                self._emit("tts_cache_skip", reason=f"served by {served}, not the pinned voice")

    async def prewarm(self, texts: list[str]) -> int:
        """Synthesize and pin sentences known to be frequent (greeting, fixed phrases) before the
        call needs them. Only clean primary-voice renders are pinned. Returns how many are cached.
        Never raises."""
        n = 0
        for text in texts:
            if (pcm := await self._cache.get(self.speaker, text)) is not None:
                n += await self._cache.offer(self.speaker, text, pcm, pin=True)
                continue
            try:
                parts = [chunk async for chunk in self._inner.stream(text)]
            except Exception:  # noqa: BLE001 - prewarming is best effort
                continue
            if getattr(self._inner, "last_provider", None) == self._primary:
                n += await self._cache.offer(self.speaker, text, b"".join(parts), pin=True)
        return n
