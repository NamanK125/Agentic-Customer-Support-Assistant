# 09_everything_down: PASS

Resilience (scripted): (a) every TTS provider refuses: the answer can't be spoken, the pre-rendered 'trouble' prompt plays and the text is logged; (b) every STT provider hangs: the turn is bounded by timeouts and 'didn't catch that' plays; (c) faults cleared: the call recovers. No hang, no crashed turn.

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (real)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.72s | FAULT | fault injected: {'target': 'tts', 'mode': 'refuse'} |
|    1.14s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|    1.57s | vad | speech start (seg 1) |
|    5.86s | vad | **end of utterance** → turn 1 (hangover 576 ms) |
|    6.66s | STT | turn 1 [groq-whisper]: "Hi, where is my order PN-10002?" (802 ms; groq-whisper:ok) |
|    6.66s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    6.66s | ASSISTANT | turn 1 ▶ filler starts |
|    7.26s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|    7.26s | provider | tts inhouse error: inhouse: connection refused (injected) |
|    7.26s | provider | tts fallback inhouse → edge (inhouse: connection refused (injected)) |
|    7.26s | provider | tts edge error: edge: connection refused (injected) |
|    9.08s | coord | tts_failed: {"error": "TTSError: fallback-tts: all TTS providers failed: inhouse:error, edge:error", "stage": "tts"} |
|    9.08s | coord | assistant_text_only: {"text": "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get a |
|    9.08s | coord | turn 1 ends: **tts_failed** (3227 ms after EOU) |
|    9.08s | coord | canned: {"name": "trouble"} |
|    9.09s | ASSISTANT | turn 1 ▶ canned trouble starts |
|   16.09s | FAULT | fault cleared: {'target': 'tts'} |
|   16.51s | FAULT | fault injected: {'target': 'stt', 'mode': 'hang'} |
|   16.93s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|   17.38s | vad | speech start (seg 2) |
|   21.63s | vad | **end of utterance** → turn 2 (hangover 576 ms) |
|   22.43s | coord | turn 2 filler "one moment" (no reply after 800 ms) |
|   22.44s | ASSISTANT | turn 2 ▶ filler starts |
|   25.63s | provider | stt groq-whisper error: groq-whisper: timed out after 4s |
|   25.63s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: timed out after 4s) |
|   35.64s | provider | stt local-whisper error: local-whisper: timed out after 10s |
|   35.64s | STT | turn 2 FAILED: STTError: fallback-stt: all STT providers failed: groq-whisper:timeout, local-whisper:timeout |
|   35.64s | coord | turn 2 ends: **stt_failed** (14003 ms after EOU) |
|   35.64s | coord | canned: {"name": "didnt_catch"} |
|   35.64s | ASSISTANT | turn 2 ▶ canned didnt_catch starts |
|   40.28s | FAULT | fault cleared: {'target': 'stt'} |
|   40.70s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|   41.19s | vad | speech start (seg 3) |
|   45.41s | vad | **end of utterance** → turn 3 (hangover 576 ms) |
|   46.08s | STT | turn 3 [groq-whisper]: "Hi, where is my order PN-10002?" (671 ms; groq-whisper:ok) |
|   46.21s | coord | turn 3 filler "one moment" (no reply after 800 ms) |
|   46.21s | ASSISTANT | turn 3 ▶ filler starts |
|   46.68s | LLM | turn 3 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|   48.64s | ASSISTANT | turn 3 ▶ answer starts |
|   68.69s | coord | turn 3 ends: **answered** (23279 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1380 (filler) | - | 576 | 803 | 602 | - | tts_failed |
| 2 | 1378 (filler) | - | 576 | 14003 | - | - | stt_failed |
| 3 | 1381 (filler) | 3804 | 576 | 672 | 601 | 1143 | first audio 1381 ms (filler); answer audio 3804 ms = hangover 576 + stt 672 + llm 601 + tts 1143 + playout 812 ms; dominated by tts (30%) |

- **to_first_audio**: n=1 p50=1381.2 ms p95=1381.2 ms

- **to_first_answer_audio**: n=1 p50=3803.8 ms p95=3803.8 ms

## Expectations

- [x] `outcomes` expected {'contains': 'tts_failed'}, got ['tts_failed', 'stt_failed', 'answered']
- [x] `stt_failed` expected 1, got 1
- [x] `turns_completed` expected 1, got 1
- [x] `canned` expected {'contains': 'trouble'}, got ['trouble', 'didnt_catch']
- [x] `no_duplicate_turns` expected True, got True
- [x] `no_dropped_turns` expected True, got True
- [x] `coordinator_errors` expected 0, got 0
- [x] `max_time_to_resolution_ms` expected {'<': 20000}, got 14001.799999999996

## Counters

```json
{
 "greeting": null,
 "turns": 3,
 "turns_answered": 1,
 "turns_completed": 1,
 "interrupted": 0,
 "merged": 0,
 "outcomes": [
  "tts_failed",
  "stt_failed",
  "answered"
 ],
 "barge_in_confirmed": 0,
 "barge_in_on_answer": 0,
 "ducks": 0,
 "unducks": 0,
 "utterances_ignored": 0,
 "vad_rejected": 0,
 "fillers": 3,
 "stale_dropped": 0,
 "replies_held": 0,
 "canned": [
  "trouble",
  "didnt_catch"
 ],
 "stt_providers": [
  "groq-whisper",
  "groq-whisper"
 ],
 "stt_texts": [
  "Hi, where is my order PN-10002?",
  "Hi, where is my order PN-10002?"
 ],
 "fallback_used": [
  "edge",
  "local-whisper"
 ],
 "tts_providers_ok": [
  "inhouse"
 ],
 "tts_failed": 1,
 "stt_failed": 1,
 "no_duplicate_turns": true,
 "no_dropped_turns": true,
 "dropped_turns": [],
 "max_turn_wall_ms": 23278.000000000007,
 "max_time_to_resolution_ms": 14001.799999999996,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 70.63,
 "wall_s": 70.63
}
```
