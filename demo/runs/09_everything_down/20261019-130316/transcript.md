# 09_everything_down: PASS

Resilience (scripted): (a) every TTS provider refuses: the answer can't be spoken, the pre-rendered 'trouble' prompt plays and the text is logged; (b) every STT provider hangs: the turn is bounded by timeouts and 'didn't catch that' plays; (c) faults cleared: the call recovers. No hang, no crashed turn.

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (SYNTHETIC)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.72s | FAULT | fault injected: {'target': 'tts', 'mode': 'refuse'} |
|    1.14s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, synthetic, 3401 ms] |
|    1.60s | vad | speech start (seg 1) |
|    4.96s | vad | **end of utterance** → turn 1 (hangover 576 ms) |
|    4.96s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|    4.96s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|    5.76s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    5.76s | ASSISTANT | turn 1 ▶ filler starts |
|    7.59s | STT | turn 1 [local-whisper]: "Hi, where is my order PN-10002?" (2626 ms; groq-whisper:error, local-whisper:ok) |
|    8.19s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|    8.19s | provider | tts inhouse error: inhouse: connection refused (injected) |
|    8.19s | provider | tts fallback inhouse → edge (inhouse: connection refused (injected)) |
|    8.19s | provider | tts edge error: edge: connection refused (injected) |
|    8.19s | coord | tts_failed: {"error": "TTSError: fallback-tts: all TTS providers failed: inhouse:error, edge:error", "stage": "tts"} |
|    8.19s | coord | assistant_text_only: {"text": "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get a |
|    8.19s | coord | turn 1 ends: **tts_failed** (3230 ms after EOU) |
|    8.19s | coord | canned: {"name": "trouble"} |
|    8.19s | ASSISTANT | turn 1 ▶ canned trouble starts |
|   14.45s | FAULT | fault cleared: {'target': 'tts'} |
|   14.87s | FAULT | fault injected: {'target': 'stt', 'mode': 'hang'} |
|   15.29s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, synthetic, 3401 ms] |
|   15.78s | vad | speech start (seg 2) |
|   19.11s | vad | **end of utterance** → turn 2 (hangover 576 ms) |
|   19.91s | coord | turn 2 filler "one moment" (no reply after 800 ms) |
|   19.91s | ASSISTANT | turn 2 ▶ filler starts |
|   23.11s | provider | stt groq-whisper error: groq-whisper: timed out after 4s |
|   23.11s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: timed out after 4s) |
|   33.11s | provider | stt local-whisper error: local-whisper: timed out after 10s |
|   33.11s | STT | turn 2 FAILED: STTError: fallback-stt: all STT providers failed: groq-whisper:timeout, local-whisper:timeout |
|   33.11s | coord | turn 2 ends: **stt_failed** (14003 ms after EOU) |
|   33.11s | coord | canned: {"name": "didnt_catch"} |
|   33.11s | ASSISTANT | turn 2 ▶ canned didnt_catch starts |
|   37.06s | FAULT | fault cleared: {'target': 'stt'} |
|   37.48s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, synthetic, 3401 ms] |
|   37.99s | vad | speech start (seg 3) |
|   41.31s | vad | **end of utterance** → turn 3 (hangover 576 ms) |
|   41.31s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|   41.31s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|   42.11s | coord | turn 3 filler "one moment" (no reply after 800 ms) |
|   42.11s | ASSISTANT | turn 3 ▶ filler starts |
|   43.38s | STT | turn 3 [local-whisper]: "Hi, where is my order PN-10002?" (2070 ms; groq-whisper:error, local-whisper:ok) |
|   43.98s | LLM | turn 3 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|   44.98s | ASSISTANT | turn 3 ▶ answer starts |
|   63.00s | coord | turn 3 ends: **answered** (21684 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1379 (filler) | - | 576 | 2628 | 602 | - | tts_failed |
| 2 | 1378 (filler) | - | 576 | 14003 | - | - | stt_failed |
| 3 | 1377 (filler) | 4239 | 576 | 2071 | 600 | 991 | first audio 1377 ms (filler); answer audio 4239 ms = hangover 576 + stt 2071 + llm 600 + tts 991 + playout 0 ms; dominated by stt (49%) |

- **to_first_audio**: n=1 p50=1376.9 ms p95=1376.9 ms

- **to_first_answer_audio**: n=1 p50=4238.7 ms p95=4238.7 ms

## Expectations

- [x] `outcomes` expected {'contains': 'tts_failed'}, got ['tts_failed', 'stt_failed', 'answered']
- [x] `stt_failed` expected 1, got 1
- [x] `turns_completed` expected 1, got 1
- [x] `canned` expected {'contains': 'trouble'}, got ['trouble', 'didnt_catch']
- [x] `no_duplicate_turns` expected True, got True
- [x] `no_dropped_turns` expected True, got True
- [x] `coordinator_errors` expected 0, got 0
- [x] `max_time_to_resolution_ms` expected {'<': 20000}, got 14003.200000000004

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
  "local-whisper",
  "local-whisper"
 ],
 "stt_texts": [
  "Hi, where is my order PN-10002?",
  "Hi, where is my order PN-10002?"
 ],
 "fallback_used": [
  "local-whisper",
  "edge",
  "local-whisper",
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
 "max_turn_wall_ms": 21683.300000000003,
 "max_time_to_resolution_ms": 14003.200000000004,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 64.92,
 "wall_s": 64.92
}
```
