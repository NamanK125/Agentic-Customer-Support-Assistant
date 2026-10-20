"""The live CLI's mic recorder must still satisfy the AudioSource protocol (frames() callable)."""

from support_assistant.voice.cli import _Recorder


class _FakeMic:
    async def frames(self):
        for _ in range(3):
            yield b"\x01\x00" * 512


async def test_recorder_yields_and_keeps_frames():
    rec = _Recorder(_FakeMic())  # type: ignore[arg-type]
    got = [f async for f in rec.frames()]
    assert len(got) == 3 and rec.recorded == got
