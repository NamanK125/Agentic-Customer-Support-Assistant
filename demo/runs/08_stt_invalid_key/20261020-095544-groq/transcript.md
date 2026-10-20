# 08_stt_invalid_key: PASS

Resilience (scripted): the primary STT (Groq Whisper) is given an invalid key (non-retryable); the chain must go straight to local faster-whisper and the turn proceeds normally.

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (real)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.72s | FAULT | fault injected: {'target': 'groq-whisper', 'mode': 'invalid_key'} |
|    1.14s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|    1.57s | vad | speech start (seg 1) |
|    5.86s | vad | **end of utterance** → turn 1 (hangover 576 ms) |
|    5.86s | provider | stt groq-whisper error: groq-whisper: 401 invalid API key (injected) |
|    5.86s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: 401 invalid API key (injected)) |
|    6.66s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    6.66s | ASSISTANT | turn 1 ▶ filler starts |
|    7.88s | STT | turn 1 [local-whisper]: "Hi, where is my order PN-10002?" (2022 ms; groq-whisper:error, local-whisper:ok) |
|    8.48s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|    9.66s | ASSISTANT | turn 1 ▶ answer starts |
|   29.69s | coord | turn 1 ends: **answered** (23832 ms after EOU) |
|   30.12s | FAULT | fault cleared: {'target': 'groq-whisper'} |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1378 (filler) | 4377 | 576 | 2023 | 602 | 1176 | first audio 1378 ms (filler); answer audio 4377 ms = hangover 576 + stt 2023 + llm 602 + tts 1176 + playout 0 ms; dominated by stt (46%) |

- **to_first_audio**: n=1 p50=1377.6 ms p95=1377.6 ms

- **to_first_answer_audio**: n=1 p50=4377.1 ms p95=4377.1 ms

## Expectations

- [x] `turns` expected 1, got 1
- [x] `turns_completed` expected 1, got 1
- [x] `stt_providers` expected 'local-whisper', got ['local-whisper']
- [x] `stt_failed` expected 0, got 0
- [x] `no_duplicate_turns` expected True, got True
- [x] `no_dropped_turns` expected True, got True
- [x] `coordinator_errors` expected 0, got 0

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
 "max_turn_wall_ms": 23831.1,
 "max_time_to_resolution_ms": 3800.3999999999996,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 32.04,
 "wall_s": 32.04
}
```
