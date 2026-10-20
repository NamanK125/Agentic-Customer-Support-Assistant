# 07_tts_failure_mid_call: PASS

Resilience (scripted): turn 1 normal; turn 2 the in-house TTS stream drops after 2 chunks (mid-sentence) and the chain restarts the sentence on Edge; turn 3 the in-house TTS refuses outright. Every turn still gets spoken audio.

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (real), return_query (real), product_question (real)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.70s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|    1.12s | vad | speech start (seg 1) |
|    5.41s | vad | **end of utterance** → turn 1 (hangover 576 ms) |
|    5.96s | STT | turn 1 [groq-whisper]: "Hi, where is my order PN-10002?" (545 ms; groq-whisper:ok) |
|    6.21s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    6.21s | ASSISTANT | turn 1 ▶ filler starts |
|    6.56s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|    8.64s | ASSISTANT | turn 1 ▶ answer starts |
|   28.66s | coord | turn 1 ends: **answered** (23248 ms after EOU) |
|   29.09s | FAULT | fault injected: {'target': 'inhouse', 'mode': 'drop_after_chunks', 'n': 2} |
|   29.51s | CALLER | "Can I return order PN-10004? I opened the box." [return_query, real, 5664 ms] |
|   29.99s | vad | speech start (seg 2) |
|   35.65s | vad | **end of utterance** → turn 2 (hangover 576 ms) |
|   36.34s | STT | turn 2 [groq-whisper]: "Can I return order PN-10004? I have opened the box." (691 ms; groq-whisper:ok) |
|   36.45s | coord | turn 2 filler "one moment" (no reply after 800 ms) |
|   36.45s | ASSISTANT | turn 2 ▶ filler starts |
|   36.94s | LLM | turn 2 reply: "Order PN-10004 is a blood pressure monitor delivered ten days ago. Since the box was opened, it can still be returned within the window, with a restocking fee. Would you like me to start the return?" |
|   38.23s | provider | tts inhouse error: inhouse: stream dropped after 2 chunks (injected) |
|   38.23s | provider | tts fallback inhouse → edge (inhouse: stream dropped after 2 chunks (injected)) (restarts that TTS request from its start) |
|   38.88s | ASSISTANT | turn 2 ▶ answer starts |
|   63.02s | coord | turn 2 ends: **answered** (27372 ms after EOU) |
|   63.45s | FAULT | fault injected: {'target': 'inhouse', 'mode': 'refuse'} |
|   63.87s | CALLER | "What does error E2 mean on my blood pressure monitor?" [product_question, real, 4320 ms] |
|   64.42s | vad | speech start (seg 3) |
|   68.71s | vad | **end of utterance** → turn 3 (hangover 576 ms) |
|   69.31s | STT | turn 3 [groq-whisper]: "What does error E2 mean on my BP monitor?" (607 ms; groq-whisper:ok) |
|   69.51s | coord | turn 3 filler "one moment" (no reply after 800 ms) |
|   69.51s | ASSISTANT | turn 3 ▶ filler starts |
|   69.91s | LLM | turn 3 reply: "Error E2 on the BP-200 means the cuff is too loose or was not inflated properly. Wrap it snugly, about two fingers' width above the elbow, and measure again." |
|   69.91s | provider | tts inhouse error: inhouse: connection refused (injected) |
|   69.91s | provider | tts fallback inhouse → edge (inhouse: connection refused (injected)) |
|   71.93s | ASSISTANT | turn 3 ▶ answer starts |
|   84.64s | coord | turn 3 ends: **answered** (15931 ms after EOU) |
|   85.06s | FAULT | fault cleared: {'target': 'inhouse'} |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1381 (filler) | 3804 | 576 | 547 | 601 | 1216 | first audio 1381 ms (filler); answer audio 3804 ms = hangover 576 + stt 547 + llm 601 + tts 1216 + playout 864 ms; dominated by tts (32%) |
| 2 | 1381 (filler) | 3803 | 576 | 692 | 601 | 1246 | first audio 1381 ms (filler); answer audio 3803 ms = hangover 576 + stt 692 + llm 601 + tts 1246 + playout 688 ms; dominated by tts (33%) |
| 3 | 1378 (filler) | 3801 | 576 | 607 | 601 | 693 | first audio 1378 ms (filler); answer audio 3801 ms = hangover 576 + stt 607 + llm 601 + tts 693 + playout 1323 ms; dominated by playout (35%) |

- **to_first_audio**: n=3 p50=1380.7 ms p95=1381.2 ms

- **to_first_answer_audio**: n=3 p50=3803.4 ms p95=3803.6 ms

## Expectations

- [x] `turns` expected 3, got 3
- [x] `turns_completed` expected 3, got 3
- [x] `tts_failed` expected 0, got 0
- [x] `fallback_used` expected 'edge', got ['edge', 'edge']
- [x] `no_duplicate_turns` expected True, got True
- [x] `no_dropped_turns` expected True, got True
- [x] `coordinator_errors` expected 0, got 0
- [x] `max_time_to_resolution_ms` expected {'<': 15000}, got 3226.800000000003

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
  "groq-whisper",
  "groq-whisper",
  "groq-whisper"
 ],
 "stt_texts": [
  "Hi, where is my order PN-10002?",
  "Can I return order PN-10004? I have opened the box.",
  "What does error E2 mean on my BP monitor?"
 ],
 "fallback_used": [
  "edge",
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
 "max_turn_wall_ms": 27371.0,
 "max_time_to_resolution_ms": 3226.800000000003,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 86.98,
 "wall_s": 86.98
}
```
