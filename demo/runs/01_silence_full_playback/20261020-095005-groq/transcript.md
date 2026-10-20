# 01_silence_full_playback: PASS

Required #1, end to end (pipeline responder): the caller asks about an order and stays silent; the whole answer plays, nothing is ducked or cut. (`replies` are used only when run with --responder scripted.)

- responder: **pipeline (build_pipeline, voice)**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (real)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.72s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|    1.15s | vad | speech start (seg 1) |
|    5.44s | vad | **end of utterance** → turn 1 (hangover 576 ms) |
|    6.23s | STT | turn 1 [groq-whisper]: "Hi, where is my order PN-10002?" (787 ms; groq-whisper:ok) |
|    6.27s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    6.27s | ASSISTANT | turn 1 ▶ filler starts |
|    9.60s | LLM | turn 1 reply: "Your order PN-10002 has been shipped via BlueDart express. The tracking ID is BD7781204IN, and the carrier's estimated delivery date is 2026-10-22. It was placed on 2026-10-17 and includes 1 unit of SKU SC-03." |
|   11.91s | ASSISTANT | turn 1 ▶ answer starts |
|   35.93s | coord | turn 1 ends: **answered** (30487 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1408 (filler) | 7042 | 576 | 789 | 3372 | 2304 | first audio 1408 ms (filler); answer audio 7042 ms = hangover 576 + stt 789 + llm 3372 + tts 2304 + playout 0 ms; dominated by llm (48%) |

- **to_first_audio**: n=1 p50=1408.5 ms p95=1408.5 ms

- **to_first_answer_audio**: n=1 p50=7042.1 ms p95=7042.1 ms

## Expectations

- [x] `turns` expected 1, got 1
- [x] `turns_completed` expected 1, got 1
- [x] `barge_in_confirmed` expected 0, got 0
- [x] `ducks` expected 0, got 0
- [x] `no_duplicate_turns` expected True, got True
- [x] `no_dropped_turns` expected True, got True
- [x] `coordinator_errors` expected 0, got 0
- [x] `max_time_to_resolution_ms` expected {'<': 30000}, got 6465.0

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
  "groq-whisper"
 ],
 "stt_texts": [
  "Hi, where is my order PN-10002?"
 ],
 "fallback_used": [],
 "tts_providers_ok": [
  "inhouse"
 ],
 "tts_failed": 0,
 "stt_failed": 0,
 "no_duplicate_turns": true,
 "no_dropped_turns": true,
 "dropped_turns": [],
 "max_turn_wall_ms": 30485.799999999996,
 "max_time_to_resolution_ms": 6465.0,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 37.86,
 "wall_s": 37.86
}
```
