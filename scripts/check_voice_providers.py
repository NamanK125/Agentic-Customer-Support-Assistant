"""Live probe of the voice providers: latency table plus a TTS -> STT round trip.

uv run python scripts/check_voice_providers.py
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from support_assistant.config import get_settings
from support_assistant.voice.contracts import pcm_ms
from support_assistant.voice.stt import GroqWhisperSTT, LocalWhisperSTT
from support_assistant.voice.tts import EdgeTTS, InHouseStreamTTS

SENTENCE = "Hello, I would like to check the status of my order. It was placed last Tuesday."


def wer(ref: str, hyp: str) -> float:
    r, h = re.findall(r"[a-z0-9']+", ref.lower()), re.findall(r"[a-z0-9']+", hyp.lower())
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[-1] / max(len(r), 1)


async def probe_tts(tts: Any) -> dict[str, Any]:
    t0 = time.perf_counter()
    first = None
    buf = bytearray()
    try:
        async for c in tts.stream(SENTENCE):
            first = first if first is not None else (time.perf_counter() - t0) * 1000
            buf += c
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    total = (time.perf_counter() - t0) * 1000
    dur = pcm_ms(bytes(buf))
    return {"ttfc": first, "total": total, "audio_ms": dur, "rtf": dur / total, "pcm": bytes(buf)}


async def main() -> None:
    s = get_settings()
    rows: list[tuple[str, str]] = []
    pcms: dict[str, bytes] = {}
    for tts in (InHouseStreamTTS(s), EdgeTTS(s)):
        r = await probe_tts(tts)
        if "error" in r:
            rows.append((f"TTS {tts.name}", f"FAILED: {r['error']}"))
            continue
        pcms[tts.name] = r["pcm"]
        rows.append(
            (
                f"TTS {tts.name}",
                f"TTFC {r['ttfc']:.0f} ms | total {r['total']:.0f} ms "
                f"| audio {r['audio_ms'] / 1000:.1f} s "
                f"| realtime x{r['rtf']:.1f}",
            )
        )
    source = pcms.get("inhouse") or pcms.get("edge")
    stts: list[Any] = [GroqWhisperSTT(s), LocalWhisperSTT(s)]
    for stt in stts:
        label = f"STT {stt.name}"
        if isinstance(stt, GroqWhisperSTT) and not stt.has_key:
            rows.append((label, "no key (set STT_API_KEY or LLM_API_KEY)"))
            continue
        if source is None:
            rows.append((label, "skipped: no TTS audio to transcribe"))
            continue
        try:
            if isinstance(stt, LocalWhisperSTT):
                t0 = time.perf_counter()
                await stt.warm_up()
                rows.append(("  local model load", f"{(time.perf_counter() - t0) * 1000:.0f} ms"))
            t = await stt.transcribe(source)
            rows.append(
                (label, f"{t.latency_ms:.0f} ms | WER {wer(SENTENCE, t.text):.0%} | {t.text!r}")
            )
        except Exception as exc:  # noqa: BLE001
            rows.append((label, f"FAILED: {exc}"))
    width = max(len(a) for a, _ in rows)
    print(f"reference: {SENTENCE!r}")
    for a, b in rows:
        print(f"{a:<{width}}  {b}")


if __name__ == "__main__":
    asyncio.run(main())
