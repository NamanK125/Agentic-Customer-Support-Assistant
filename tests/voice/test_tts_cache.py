import struct

import pytest

from support_assistant.voice.contracts import TTSError
from support_assistant.voice.tts import strip_wav_header
from support_assistant.voice.tts_cache import CachingTTS, TTSAudioCache


def wav(pcm: bytes, rate: int = 16000, channels: int = 1, bits: int = 16) -> bytes:
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * bits // 8, channels * 2, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_wav_header_is_stripped_not_played():
    pcm = b"\x01\x00\x02\x00" * 10
    assert strip_wav_header(wav(pcm), "inhouse") == pcm
    assert len(wav(pcm)) - len(pcm) == 44  # the 44 bytes that used to click
    assert strip_wav_header(pcm, "inhouse") == pcm  # raw PCM passes through


def test_wrong_wav_format_is_refused():
    with pytest.raises(TTSError):
        strip_wav_header(wav(b"\x00\x00" * 4, rate=24000), "inhouse")


class _FakeChain:
    """Stands in for FallbackTTS: yields fixed audio, reports which provider served it."""

    def __init__(self, provider: str = "inhouse") -> None:
        self.calls = 0
        self.last_provider: str | None = None
        self.provider = provider

    async def stream(self, text):
        self.calls += 1
        self.last_provider = self.provider
        yield f"audio:{text}".encode().ljust(64, b"\x00")


async def _play(tts, text):
    return b"".join([c async for c in tts.stream(text)])


async def test_admitted_only_after_repeat_then_served_from_cache():
    cache = TTSAudioCache(disk_dir=None, admit_after=2)
    chain = _FakeChain()
    tts = CachingTTS(chain, cache, speaker="A", primary="inhouse")
    for _ in range(4):
        await _play(tts, "How can I help you today?")
    assert chain.calls == 2  # synthesized on requests 1 and 2, cached from then on
    assert cache.hits == 2


async def test_one_off_sentences_are_not_stored():
    cache = TTSAudioCache(disk_dir=None, admit_after=2)
    tts = CachingTTS(_FakeChain(), cache, speaker="A", primary="inhouse")
    await _play(tts, "Your order PN-10002 ships Tuesday.")
    assert cache.stats()["entries"] == 0


async def test_cache_never_crosses_speakers():
    cache = TTSAudioCache(disk_dir=None, admit_after=1)
    a = CachingTTS(_FakeChain(), cache, speaker="A", primary="inhouse")
    chain_b = _FakeChain()
    b = CachingTTS(chain_b, cache, speaker="B", primary="inhouse")
    await _play(a, "Hello.")
    await _play(a, "Hello.")
    await _play(b, "Hello.")
    assert chain_b.calls == 1  # speaker B synthesized its own; it never got A's audio


async def test_fallback_voice_and_cancelled_streams_are_not_cached():
    cache = TTSAudioCache(disk_dir=None, admit_after=1)
    edge = CachingTTS(_FakeChain(provider="edge"), cache, speaker="A", primary="inhouse")
    await _play(edge, "Hello.")
    await _play(edge, "Hello.")
    assert cache.stats()["entries"] == 0  # served in the fallback voice: never cached

    tts = CachingTTS(_FakeChain(), cache, speaker="A", primary="inhouse")
    it = tts.stream("Bye.").__aiter__()
    await it.__anext__()
    await it.aclose()  # barge-in mid-sentence
    assert cache.stats()["entries"] == 0


async def test_lfu_eviction_keeps_frequent_and_pinned():
    cache = TTSAudioCache(disk_dir=None, admit_after=1, max_bytes=200)
    tts = CachingTTS(_FakeChain(), cache, speaker="A", primary="inhouse")
    await tts.prewarm(["Greeting."])  # pinned
    for _ in range(3):
        await _play(tts, "Popular.")
    await _play(tts, "Rare one.")
    await _play(tts, "Rare two.")  # over budget: a rare entry goes, not Popular or Greeting
    kept = {s["text"] for s in cache.stats()["top_sentences"]}
    assert await cache.get("A", "Popular.") is not None
    assert await cache.get("A", "Greeting.") is not None
    assert "Popular." in kept


async def test_digits_never_written_to_disk(tmp_path):
    cache = TTSAudioCache(disk_dir=tmp_path, admit_after=1)
    tts = CachingTTS(_FakeChain(), cache, speaker="A", primary="inhouse")
    await _play(tts, "Order PN-10002 shipped.")
    await _play(tts, "Thank you for calling.")
    files = list(tmp_path.glob("*.pcm"))
    assert len(files) == 1  # only the digit-free sentence persisted
