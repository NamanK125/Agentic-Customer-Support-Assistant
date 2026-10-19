"""One call, one speaker id: every request the call sends to the TTS service uses TTS_SPEAKER."""

import json

import websockets

from support_assistant.config import Settings
from support_assistant.voice.canned import CannedAudio
from support_assistant.voice.faults import FaultInjector
from support_assistant.voice.providers import build_tts, call_settings

SPEAKER = "RJ_BP_SDV, moderate pitch"


async def test_every_tts_request_in_a_call_uses_the_same_speaker(tmp_path):
    configs: list[dict] = []

    async def fake_tts(ws):
        configs.append(json.loads(await ws.recv()))
        async for raw in ws:
            msg = json.loads(raw)
            if msg["type"] == "flush":
                await ws.send(json.dumps({"type": "chunk", "chunk_number": 1}))
                await ws.send(b"\x01\x00" * 1600)
                await ws.send(json.dumps({"type": "done"}))
            elif msg["type"] == "client_complete":
                return

    async with websockets.serve(fake_tts, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        settings = Settings(_env_file=None, tts_ws_url=f"ws://127.0.0.1:{port}/v1/tts/stream")
        assert settings.tts_speaker == SPEAKER

        cs = call_settings(settings)
        tts = build_tts(cs, FaultInjector(), session_label="call-1")
        await CannedAudio(cs, cache_dir=tmp_path).prepare(tts)  # canned phrases
        for reply in ["Order PN-10002 has shipped.", "Anything else?", "Anything else?"]:
            _ = [chunk async for chunk in tts.stream(reply)]  # replies

    assert len(configs) >= 5
    assert {c["speaker_id"] for c in configs} == {SPEAKER}
    # normalization and phonemization are on, identically, on every request
    assert all(c["skip_normalize_input"] is False for c in configs)
    assert all(c["transliterate_capitals"] is True for c in configs)
    labels = [c["tts_session_id"] for c in configs]
    assert all(label.startswith("call-1-r") for label in labels)
    assert len(set(labels)) == len(labels)  # one label per request, all under the call's prefix
