# 10_latency_run: PASS

Latency (pipeline, end to end): 10 ordinary turns, no interruptions, for p50/p95 of end-of-speech -> first audio (filler included) and -> first answer audio. (`replies` are used only when run with --responder scripted.)

- responder: **pipeline (build_pipeline, voice)**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (real), return_query (real), product_question (real), talk_to_human (real)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.70s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|    1.12s | vad | speech start (seg 1) |
|    5.41s | vad | **end of utterance** → turn 1 (hangover 576 ms) |
|    6.06s | STT | turn 1 [groq-whisper]: "Hi, where is my order PN-10002?" (647 ms; groq-whisper:ok) |
|    6.21s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    6.21s | ASSISTANT | turn 1 ▶ filler starts |
|    8.81s | LLM | turn 1 reply: "Your order PN-10002 has been shipped via BlueDart express. The tracking ID is BD7781204IN, and the carrier's estimated delivery date is 2026-10-22. It was placed on 2026-10-17 and includes 1 unit of SKU SC-03." |
|    9.90s | ASSISTANT | turn 1 ▶ answer starts |
|   33.94s | coord | turn 1 ends: **answered** (28534 ms after EOU) |
|   34.38s | CALLER | "Can I return order PN-10004? I opened the box." [return_query, real, 5664 ms] |
|   34.85s | vad | speech start (seg 2) |
|   40.51s | vad | **end of utterance** → turn 2 (hangover 576 ms) |
|   41.20s | STT | turn 2 [groq-whisper]: "Can I return order PN-10004? I have opened the box." (688 ms; groq-whisper:ok) |
|   41.32s | coord | turn 2 filler "one moment" (no reply after 800 ms) |
|   41.32s | ASSISTANT | turn 2 ▶ filler starts |
|   45.27s | LLM | turn 2 reply: "Yes, order PN-10004 (SKU BP-200) is eligible for return since it was delivered on 2026-10-10 and is within the 15-day window, which ends on 2026-10-25. A 10% restocking fee applies because the item was opened. The final refund amount is confirmed only after the item passes inspection at our warehouse; the refund then reaches your original payment method within 5-7 business days." |
|   46.48s | ASSISTANT | turn 2 ▶ answer starts |
|   81.53s | coord | turn 2 ends: **answered** (41018 ms after EOU) |
|   81.96s | CALLER | "What does error E2 mean on my blood pressure monitor?" [product_question, real, 4320 ms] |
|   82.47s | vad | speech start (seg 3) |
|   86.79s | vad | **end of utterance** → turn 3 (hangover 576 ms) |
|   87.45s | STT | turn 3 [groq-whisper]: "What does error E2 mean on my BP monitor?" (668 ms; groq-whisper:ok) |
|   87.59s | coord | turn 3 filler "one moment" (no reply after 800 ms) |
|   87.59s | ASSISTANT | turn 3 ▶ filler starts |
|   89.61s | LLM | turn 3 reply: "Error E2 on the BP-200 indicates that movement or talking was detected during the measurement [2]. To resolve this, sit with your back supported, feet flat, and arm resting at heart level while staying still and quiet until the reading finishes [2]. If the error persists, you can contact support with the error code [1]." |
|   91.06s | ASSISTANT | turn 3 ▶ answer starts |
|  115.00s | coord | turn 3 ends: **answered** (28211 ms after EOU) |
|  115.42s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|  115.87s | vad | speech start (seg 4) |
|  120.13s | vad | **end of utterance** → turn 4 (hangover 576 ms) |
|  120.73s | STT | turn 4 [groq-whisper]: "Hi, where is my order PN-10002?" (601 ms; groq-whisper:ok) |
|  120.93s | coord | turn 4 filler "one moment" (no reply after 800 ms) |
|  120.93s | ASSISTANT | turn 4 ▶ filler starts |
|  123.81s | LLM | turn 4 reply: "Your order PN-10002 (SKU SC-03) has been shipped via BlueDart express, with tracking number BD7781204IN. The carrier's estimated delivery date is 2026-10-22. You can use the tracking number to follow its progress." |
|  125.00s | ASSISTANT | turn 4 ▶ answer starts |
|  146.94s | coord | turn 4 ends: **answered** (26813 ms after EOU) |
|  147.38s | CALLER | "I want to talk to a real person, please." [talk_to_human, real, 2560 ms] |
|  147.87s | vad | speech start (seg 5) |
|  150.43s | vad | **end of utterance** → turn 5 (hangover 576 ms) |
|  151.04s | STT | turn 5 [groq-whisper]: "I want to talk to a real person please." (610 ms; groq-whisper:ok) |
|  151.24s | coord | turn 5 filler "one moment" (no reply after 800 ms) |
|  151.24s | ASSISTANT | turn 5 ▶ filler starts |
|  152.89s | LLM | turn 5 reply: "Human agents are available 9:00–21:00 IST, every day. A member of our team will pick it up shortly. I can't share details about how I'm set up internally." |
|  154.14s | ASSISTANT | turn 5 ▶ answer starts |
|  167.08s | coord | turn 5 ends: **answered** (16644 ms after EOU) |
|  167.50s | CALLER | "What does error E2 mean on my blood pressure monitor?" [product_question, real, 4320 ms] |
|  168.03s | vad | speech start (seg 6) |
|  172.35s | vad | **end of utterance** → turn 6 (hangover 576 ms) |
|  172.91s | STT | turn 6 [groq-whisper]: "What does error E2 mean on my BP monitor?" (555 ms; groq-whisper:ok) |
|  173.16s | coord | turn 6 filler "one moment" (no reply after 800 ms) |
|  173.16s | ASSISTANT | turn 6 ▶ filler starts |
|  175.61s | LLM | turn 6 reply: "Error E2 on the BP-200 indicates that movement or talking was detected during the measurement [2]. To resolve this, sit with your back supported, feet flat, and arm resting at heart level while staying still and quiet until the reading finishes [2]. If the error persists, you can contact support with the error code [1]." |
|  177.08s | ASSISTANT | turn 6 ▶ answer starts |
|  200.87s | coord | turn 6 ends: **answered** (28513 ms after EOU) |
|  201.30s | CALLER | "Can I return order PN-10004? I opened the box." [return_query, real, 5664 ms] |
|  201.76s | vad | speech start (seg 7) |
|  207.43s | vad | **end of utterance** → turn 7 (hangover 576 ms) |
|  208.10s | STT | turn 7 [groq-whisper]: "Can I return order PN-10004? I have opened the box." (671 ms; groq-whisper:ok) |
|  208.23s | coord | turn 7 filler "one moment" (no reply after 800 ms) |
|  208.23s | ASSISTANT | turn 7 ▶ filler starts |
|  211.26s | LLM | turn 7 reply: "Yes, your BP-200 in order PN-10004 is eligible for return, as it was delivered on 2026-10-10 and you're within the 15-day window ending 2026-10-25. A 10% restocking fee applies because the item was opened. The final refund amount is confirmed only after the item passes inspection at our warehouse; the refund then reaches your original payment method within 5-7 business days." |
|  212.56s | ASSISTANT | turn 7 ▶ answer starts |
|  246.00s | coord | turn 7 ends: **answered** (38577 ms after EOU) |
|  246.44s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|  246.91s | vad | speech start (seg 8) |
|  251.17s | vad | **end of utterance** → turn 8 (hangover 576 ms) |
|  251.93s | STT | turn 8 [groq-whisper]: "Hi, where is my order PN-10002?" (760 ms; groq-whisper:ok) |
|  251.97s | coord | turn 8 filler "one moment" (no reply after 800 ms) |
|  251.97s | ASSISTANT | turn 8 ▶ filler starts |
|  255.21s | LLM | turn 8 reply: "Your order PN-10002 (item SC-03) has been shipped via BlueDart express, with tracking number BD7781204IN. The carrier's estimated delivery date is 2026-10-22. It was placed on 2026-10-17 and is currently in transit." |
|  256.56s | ASSISTANT | turn 8 ▶ answer starts |
|  280.46s | coord | turn 8 ends: **answered** (29287 ms after EOU) |
|  280.88s | CALLER | "What does error E2 mean on my blood pressure monitor?" [product_question, real, 4320 ms] |
|  281.41s | vad | speech start (seg 9) |
|  285.73s | vad | **end of utterance** → turn 9 (hangover 576 ms) |
|  286.42s | STT | turn 9 [groq-whisper]: "What does error E2 mean on my BP monitor?" (686 ms; groq-whisper:ok) |
|  286.53s | coord | turn 9 filler "one moment" (no reply after 800 ms) |
|  286.53s | ASSISTANT | turn 9 ▶ filler starts |
|  289.21s | LLM | turn 9 reply: "Error E2 on the BP-200 indicates that movement or talking was detected during the measurement [2]. To resolve this, sit with your back supported, feet flat, and arm resting at heart level while staying still and quiet until the reading finishes [2]. If the error persists, you can contact support with the error code [1]." |
|  290.49s | ASSISTANT | turn 9 ▶ answer starts |
|  314.18s | coord | turn 9 ends: **answered** (28450 ms after EOU) |
|  314.61s | CALLER | "Can I return order PN-10004? I opened the box." [return_query, real, 5664 ms] |
|  315.07s | vad | speech start (seg 10) |
|  320.74s | vad | **end of utterance** → turn 10 (hangover 576 ms) |
|  321.42s | STT | turn 10 [groq-whisper]: "Can I return order PN-10004? I have opened the box." (685 ms; groq-whisper:ok) |
|  321.55s | coord | turn 10 filler "one moment" (no reply after 800 ms) |
|  321.55s | ASSISTANT | turn 10 ▶ filler starts |
|  324.16s | LLM | turn 10 reply: "Yes, your BP-200 (order PN-10004) is eligible for return since it was delivered on 2026-10-10 and you're within the 15-day window, which ends on 2026-10-25. A 10% restocking fee applies to opened items. The final refund amount is confirmed only after the item passes inspection at our warehouse; the refund then reaches your original payment method within 5-7 business days." |
|  325.48s | ASSISTANT | turn 10 ▶ answer starts |
|  359.55s | coord | turn 10 ends: **answered** (38817 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1381 (filler) | 5071 | 576 | 647 | 2755 | 1092 | first audio 1381 ms (filler); answer audio 5071 ms = hangover 576 + stt 647 + llm 2755 + tts 1092 + playout 0 ms; dominated by llm (54%) |
| 2 | 1380 (filler) | 6546 | 576 | 689 | 4066 | 1215 | first audio 1380 ms (filler); answer audio 6546 ms = hangover 576 + stt 689 + llm 4066 + tts 1215 + playout 0 ms; dominated by llm (62%) |
| 3 | 1380 (filler) | 4852 | 576 | 669 | 2152 | 1455 | first audio 1380 ms (filler); answer audio 4852 ms = hangover 576 + stt 669 + llm 2152 + tts 1455 + playout 0 ms; dominated by llm (44%) |
| 4 | 1381 (filler) | 5447 | 576 | 602 | 3076 | 1193 | first audio 1381 ms (filler); answer audio 5447 ms = hangover 576 + stt 602 + llm 3076 + tts 1193 + playout 0 ms; dominated by llm (56%) |
| 5 | 1380 (filler) | 4284 | 576 | 610 | 1844 | 1253 | first audio 1380 ms (filler); answer audio 4284 ms = hangover 576 + stt 610 + llm 1844 + tts 1253 + playout 0 ms; dominated by llm (43%) |
| 6 | 1380 (filler) | 5306 | 576 | 555 | 2704 | 1471 | first audio 1380 ms (filler); answer audio 5306 ms = hangover 576 + stt 555 + llm 2704 + tts 1471 + playout 0 ms; dominated by llm (51%) |
| 7 | 1381 (filler) | 5710 | 576 | 672 | 3159 | 1302 | first audio 1381 ms (filler); answer audio 5710 ms = hangover 576 + stt 672 + llm 3159 + tts 1302 + playout 0 ms; dominated by llm (55%) |
| 8 | 1380 (filler) | 5963 | 576 | 761 | 3283 | 1343 | first audio 1380 ms (filler); answer audio 5963 ms = hangover 576 + stt 761 + llm 3283 + tts 1343 + playout 0 ms; dominated by llm (55%) |
| 9 | 1381 (filler) | 5334 | 576 | 687 | 2797 | 1273 | first audio 1381 ms (filler); answer audio 5334 ms = hangover 576 + stt 687 + llm 2797 + tts 1273 + playout 0 ms; dominated by llm (52%) |
| 10 | 1387 (filler) | 5316 | 576 | 686 | 2736 | 1318 | first audio 1387 ms (filler); answer audio 5316 ms = hangover 576 + stt 686 + llm 2736 + tts 1318 + playout 0 ms; dominated by llm (51%) |

- **to_first_audio**: n=10 p50=1380.5 ms p95=1386.7 ms

- **to_first_answer_audio**: n=10 p50=5315.9 ms p95=6546.1 ms

## Expectations

- [x] `turns` expected 10, got 10
- [x] `turns_completed` expected 10, got 10
- [x] `no_duplicate_turns` expected True, got True
- [x] `no_dropped_turns` expected True, got True
- [x] `coordinator_errors` expected 0, got 0

## Counters

```json
{
 "greeting": null,
 "turns": 10,
 "turns_answered": 10,
 "turns_completed": 10,
 "interrupted": 0,
 "merged": 0,
 "outcomes": [
  "answered",
  "answered",
  "answered",
  "answered",
  "answered",
  "answered",
  "answered",
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
 "fillers": 10,
 "stale_dropped": 0,
 "replies_held": 0,
 "canned": [],
 "stt_providers": [
  "groq-whisper",
  "groq-whisper",
  "groq-whisper",
  "groq-whisper",
  "groq-whisper",
  "groq-whisper",
  "groq-whisper",
  "groq-whisper",
  "groq-whisper",
  "groq-whisper"
 ],
 "stt_texts": [
  "Hi, where is my order PN-10002?",
  "Can I return order PN-10004? I have opened the box.",
  "What does error E2 mean on my BP monitor?",
  "Hi, where is my order PN-10002?",
  "I want to talk to a real person please.",
  "What does error E2 mean on my BP monitor?",
  "Can I return order PN-10004? I have opened the box.",
  "Hi, where is my order PN-10002?",
  "What does error E2 mean on my BP monitor?",
  "Can I return order PN-10004? I have opened the box."
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
 "max_turn_wall_ms": 41017.3,
 "max_time_to_resolution_ms": 5969.299999999996,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 361.48,
 "wall_s": 361.48
}
```
