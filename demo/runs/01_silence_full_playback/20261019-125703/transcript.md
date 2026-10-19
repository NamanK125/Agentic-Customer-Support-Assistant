# 01_silence_full_playback: PASS

Required #1, end to end (pipeline responder): the caller asks about an order and stays silent; the whole answer plays, nothing is ducked or cut. (`replies` are used only when run with --responder scripted.)

- responder: **pipeline (build_pipeline, voice)**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (SYNTHETIC)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.72s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, synthetic, 3401 ms] |
|    1.19s | vad | speech start (seg 1) |
|    4.55s | vad | **end of utterance** → turn 1 (hangover 576 ms) |
|    4.55s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|    4.55s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|    5.35s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    5.35s | ASSISTANT | turn 1 ▶ filler starts |
|    6.52s | STT | turn 1 [local-whisper]: "Hi, where is my order PN-10002?" (1971 ms; groq-whisper:error, local-whisper:ok) |
|   14.18s | LLM | turn 1 reply: "Order PN-10002 is shipped. It is with BlueDart, tracking number BD7781204IN. The carrier's estimated delivery date is 2026-10-21." |
|   14.99s | ASSISTANT | turn 1 ▶ answer starts |
|   29.21s | coord | turn 1 ends: **answered** (24662 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1379 (filler) | 11018 | 576 | 1973 | 7662 | 807 | first audio 1379 ms (filler); answer audio 11018 ms = hangover 576 + stt 1973 + llm 7662 + tts 807 + playout 0 ms; dominated by llm (70%) |

- **to_first_audio**: n=1 p50=1378.9 ms p95=1378.9 ms

- **to_first_answer_audio**: n=1 p50=11018.4 ms p95=11018.4 ms

## Expectations

- [x] `turns` expected 1, got 1
- [x] `turns_completed` expected 1, got 1
- [x] `barge_in_confirmed` expected 0, got 0
- [x] `ducks` expected 0, got 0
- [x] `no_duplicate_turns` expected True, got True
- [x] `no_dropped_turns` expected True, got True
- [x] `coordinator_errors` expected 0, got 0
- [x] `max_time_to_resolution_ms` expected {'<': 30000}, got 10440.8

## Counters

```json
{
 "greeting": null,
 "turns": 1,
 "turns_answered": 1,
 "turns_completed": 1,
 "interrupted": 0,
 "merged": 0,
 "outcomes": [
  "answered"
 ],
 "barge_in_confirmed": 0,
 "barge_in_on_answer": 0,
 "ducks": 0,
 "unducks": 0,
 "utterances_ignored": 0,
 "vad_rejected": 0,
 "fillers": 1,
 "stale_dropped": 0,
 "replies_held": 0,
 "canned": [],
 "stt_providers": [
  "local-whisper"
 ],
 "stt_texts": [
  "Hi, where is my order PN-10002?"
 ],
 "fallback_used": [
  "local-whisper"
 ],
 "tts_providers_ok": [
  "inhouse"
 ],
 "tts_failed": 0,
 "stt_failed": 0,
 "no_duplicate_turns": true,
 "no_dropped_turns": true,
 "dropped_turns": [],
 "max_turn_wall_ms": 24660.199999999997,
 "max_time_to_resolution_ms": 10440.8,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 31.13,
 "wall_s": 31.13
}
```
