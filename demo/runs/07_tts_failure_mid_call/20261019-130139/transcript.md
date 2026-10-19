# 07_tts_failure_mid_call: PASS

Resilience (scripted): turn 1 normal; turn 2 the in-house TTS stream drops after 2 chunks (mid-sentence) and the chain restarts the sentence on Edge; turn 3 the in-house TTS refuses outright. Every turn still gets spoken audio.

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (SYNTHETIC), return_query (SYNTHETIC), product_question (SYNTHETIC)
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
|    6.74s | STT | turn 1 [local-whisper]: "Hi, where is my order PN-10002?" (2191 ms; groq-whisper:error, local-whisper:ok) |
|    7.34s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|    8.42s | ASSISTANT | turn 1 ▶ answer starts |
|   26.39s | coord | turn 1 ends: **answered** (21846 ms after EOU) |
|   26.82s | FAULT | fault injected: {'target': 'inhouse', 'mode': 'drop_after_chunks', 'n': 2} |
|   27.24s | CALLER | "Can I return order PN-10004? I opened the box." [return_query, synthetic, 4413 ms] |
|   27.75s | vad | speech start (seg 2) |
|   32.07s | vad | **end of utterance** → turn 2 (hangover 576 ms) |
|   32.07s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|   32.07s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|   32.87s | coord | turn 2 filler "one moment" (no reply after 800 ms) |
|   32.87s | ASSISTANT | turn 2 ▶ filler starts |
|   34.26s | STT | turn 2 [local-whisper]: "Can I return order PN-10004? I opened the box." (2192 ms; groq-whisper:error, local-whisper:ok) |
|   34.86s | LLM | turn 2 reply: "Order PN-10004 is a blood pressure monitor delivered ten days ago. Since the box was opened, it can still be returned within the window, with a restocking fee. Would you like me to start the return?" |
|   35.87s | ASSISTANT | turn 2 ▶ answer starts |
|   35.95s | provider | tts inhouse error: inhouse: stream dropped after 2 chunks (injected) |
|   35.95s | provider | tts fallback inhouse → edge (inhouse: stream dropped after 2 chunks (injected)) restart sentence |
|   57.45s | coord | turn 2 ends: **answered** (25388 ms after EOU) |
|   57.89s | FAULT | fault injected: {'target': 'inhouse', 'mode': 'refuse'} |
|   58.31s | CALLER | "What does error E2 mean on my blood pressure monitor?" [product_question, synthetic, 2841 ms] |
|   58.82s | vad | speech start (seg 3) |
|   61.57s | vad | **end of utterance** → turn 3 (hangover 576 ms) |
|   61.57s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|   61.57s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|   62.37s | coord | turn 3 filler "one moment" (no reply after 800 ms) |
|   62.37s | ASSISTANT | turn 3 ▶ filler starts |
|   64.30s | STT | turn 3 [local-whisper]: "What does error E2 mean on my blood pressure monitor?" (2727 ms; groq-whisper:error, local-whisper:ok) |
|   64.90s | LLM | turn 3 reply: "Error E2 on the BP-200 means the cuff is too loose or was not inflated properly. Wrap it snugly, about two fingers' width above the elbow, and measure again." |
|   64.90s | provider | tts inhouse error: inhouse: connection refused (injected) |
|   64.90s | provider | tts fallback inhouse → edge (inhouse: connection refused (injected)) |
|   65.65s | ASSISTANT | turn 3 ▶ answer starts |
|   76.86s | coord | turn 3 ends: **answered** (15290 ms after EOU) |
|   77.27s | FAULT | fault cleared: {'target': 'inhouse'} |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1378 (filler) | 4452 | 576 | 2192 | 601 | 1083 | first audio 1378 ms (filler); answer audio 4452 ms = hangover 576 + stt 2192 + llm 601 + tts 1083 + playout 0 ms; dominated by stt (49%) |
| 2 | 1377 (filler) | 4377 | 576 | 2192 | 601 | 1008 | first audio 1377 ms (filler); answer audio 4377 ms = hangover 576 + stt 2192 + llm 601 + tts 1008 + playout 0 ms; dominated by stt (50%) |
| 3 | 1378 (filler) | 4656 | 576 | 2729 | 600 | 750 | first audio 1378 ms (filler); answer audio 4656 ms = hangover 576 + stt 2729 + llm 600 + tts 750 + playout 0 ms; dominated by stt (59%) |

- **to_first_audio**: n=3 p50=1377.9 ms p95=1378.3 ms

- **to_first_answer_audio**: n=3 p50=4452.2 ms p95=4655.5 ms

## Expectations

- [x] `turns` expected 3, got 3
- [x] `turns_completed` expected 3, got 3
- [x] `tts_failed` expected 0, got 0
- [x] `fallback_used` expected 'edge', got ['local-whisper', 'local-whisper', 'edge', 'local-whisper', 'edge']
- [x] `no_duplicate_turns` expected True, got True
- [x] `no_dropped_turns` expected True, got True
- [x] `coordinator_errors` expected 0, got 0
- [x] `max_time_to_resolution_ms` expected {'<': 15000}, got 4078.199999999997

## Counters

```json
{
 "greeting": null,
 "turns": 3,
 "turns_answered": 3,
 "turns_completed": 3,
 "interrupted": 0,
 "merged": 0,
 "outcomes": [
  "answered",
  "answered",
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
 "canned": [],
 "stt_providers": [
  "local-whisper",
  "local-whisper",
  "local-whisper"
 ],
 "stt_texts": [
  "Hi, where is my order PN-10002?",
  "Can I return order PN-10004? I opened the box.",
  "What does error E2 mean on my blood pressure monitor?"
 ],
 "fallback_used": [
  "local-whisper",
  "local-whisper",
  "edge",
  "local-whisper",
  "edge"
 ],
 "tts_providers_ok": [
  "edge",
  "inhouse"
 ],
 "tts_failed": 0,
 "stt_failed": 0,
 "no_duplicate_turns": true,
 "no_dropped_turns": true,
 "dropped_turns": [],
 "max_turn_wall_ms": 25387.5,
 "max_time_to_resolution_ms": 4078.199999999997,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 79.19,
 "wall_s": 79.19
}
```
