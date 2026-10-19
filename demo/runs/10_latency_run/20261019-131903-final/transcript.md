# 10_latency_run: PASS

Latency (pipeline, end to end): 10 ordinary turns, no interruptions, for p50/p95 of end-of-speech -> first audio (filler included) and -> first answer audio. (`replies` are used only when run with --responder scripted.)

- responder: **pipeline (build_pipeline, voice)**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (SYNTHETIC), return_query (SYNTHETIC), product_question (SYNTHETIC), talk_to_human (SYNTHETIC)
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
|    7.00s | STT | turn 1 [local-whisper]: "Hi, where is my order PN-10002?" (2455 ms; groq-whisper:error, local-whisper:ok) |
|   14.56s | LLM | turn 1 reply: "Order PN-10002 is shipped. It is with BlueDart, tracking number BD7781204IN. The carrier's estimated delivery date is 2026-10-21." |
|   15.41s | ASSISTANT | turn 1 ▶ answer starts |
|   28.05s | coord | turn 1 ends: **answered** (23502 ms after EOU) |
|   28.47s | CALLER | "Can I return order PN-10004? I opened the box." [return_query, synthetic, 4413 ms] |
|   28.96s | vad | speech start (seg 2) |
|   33.28s | vad | **end of utterance** → turn 2 (hangover 576 ms) |
|   33.28s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|   33.28s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|   34.08s | coord | turn 2 filler "one moment" (no reply after 800 ms) |
|   34.08s | ASSISTANT | turn 2 ▶ filler starts |
|   35.32s | STT | turn 2 [local-whisper]: "Can I return order PN-10004? I open the box." (2035 ms; groq-whisper:error, local-whisper:ok) |
|   42.28s | LLM | turn 2 reply: "The BP-200 from order PN-10004 is eligible for return until 2026-10-24. Opened item within the 15-day return window with a 10% restocking fee. The final refund amount is confirmed only after the item passes inspection at our warehouse; the refund then reaches your original payment method within 5-7 business days." |
|   43.34s | ASSISTANT | turn 2 ▶ answer starts |
|   66.84s | coord | turn 2 ends: **answered** (33559 ms after EOU) |
|   67.27s | CALLER | "What does error E2 mean on my blood pressure monitor?" [product_question, synthetic, 2841 ms] |
|   67.75s | vad | speech start (seg 3) |
|   70.50s | vad | **end of utterance** → turn 3 (hangover 576 ms) |
|   70.50s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|   70.50s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|   71.30s | coord | turn 3 filler "one moment" (no reply after 800 ms) |
|   71.30s | ASSISTANT | turn 3 ▶ filler starts |
|   72.57s | STT | turn 3 [local-whisper]: "What does error E2 mean on my blood pressure monitor?" (2075 ms; groq-whisper:error, local-whisper:ok) |
|   85.23s | LLM | turn 3 reply: "Error E2 on your BP-200 indicates that movement or talking was detected during the measurement [1]. To resolve this, sit with your back supported, keep your feet flat, and rest your arm at heart level on a table while staying still and quiet until the reading finishes [1]. You should then try the measurement again [1]." |
|   86.35s | ASSISTANT | turn 3 ▶ answer starts |
|  105.93s | coord | turn 3 ends: **answered** (35437 ms after EOU) |
|  106.37s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, synthetic, 3401 ms] |
|  106.82s | vad | speech start (seg 4) |
|  110.18s | vad | **end of utterance** → turn 4 (hangover 576 ms) |
|  110.18s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|  110.18s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|  110.98s | coord | turn 4 filler "one moment" (no reply after 800 ms) |
|  110.98s | ASSISTANT | turn 4 ▶ filler starts |
|  112.21s | STT | turn 4 [local-whisper]: "Hi, where is my order PN-10002?" (2034 ms; groq-whisper:error, local-whisper:ok) |
|  119.17s | LLM | turn 4 reply: "Order PN-10002 is shipped. It is with BlueDart, tracking number BD7781204IN. The carrier's estimated delivery date is 2026-10-21." |
|  119.96s | ASSISTANT | turn 4 ▶ answer starts |
|  132.64s | coord | turn 4 ends: **answered** (22465 ms after EOU) |
|  133.08s | CALLER | "I want to talk to a real person, please." [talk_to_human, synthetic, 2141 ms] |
|  133.54s | vad | speech start (seg 5) |
|  135.65s | vad | **end of utterance** → turn 5 (hangover 576 ms) |
|  136.45s | coord | turn 5 filler "one moment" (no reply after 800 ms) |
|  136.45s | ASSISTANT | turn 5 ▶ filler starts |
|  137.84s | STT | turn 5 [local-whisper]: "I want to talk to a real person, please." (2193 ms; groq-whisper:circuit_open, local-whisper:ok) |
|  144.25s | LLM | turn 5 reply: "I've passed this to our support team, ticket HT-8C7EE8B3. Human agents are available 9:00–21:00 IST, every day. A member of our team will pick it up shortly." |
|  145.09s | ASSISTANT | turn 5 ▶ answer starts |
|  157.96s | coord | turn 5 ends: **answered** (22308 ms after EOU) |
|  158.38s | CALLER | "What does error E2 mean on my blood pressure monitor?" [product_question, synthetic, 2841 ms] |
|  158.88s | vad | speech start (seg 6) |
|  161.63s | vad | **end of utterance** → turn 6 (hangover 576 ms) |
|  161.63s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|  161.63s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|  162.44s | coord | turn 6 filler "one moment" (no reply after 800 ms) |
|  162.44s | ASSISTANT | turn 6 ▶ filler starts |
|  163.87s | STT | turn 6 [local-whisper]: "What does error E2 mean on my blood pressure monitor?" (2238 ms; groq-whisper:error, local-whisper:ok) |
|  176.34s | LLM | turn 6 reply: "Error E2 on your BP-200 indicates that movement or talking was detected during the measurement [1]. To resolve this, sit with your back supported, keep your feet flat, and rest your arm at heart level on a table while staying still and quiet until the reading finishes [1]. You should then try the measurement again [1]." |
|  177.38s | ASSISTANT | turn 6 ▶ answer starts |
|  196.62s | coord | turn 6 ends: **answered** (34982 ms after EOU) |
|  197.05s | CALLER | "Can I return order PN-10004? I opened the box." [return_query, synthetic, 4413 ms] |
|  197.51s | vad | speech start (seg 7) |
|  201.86s | vad | **end of utterance** → turn 7 (hangover 576 ms) |
|  201.86s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|  201.86s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|  202.66s | coord | turn 7 filler "one moment" (no reply after 800 ms) |
|  202.66s | ASSISTANT | turn 7 ▶ filler starts |
|  204.07s | STT | turn 7 [local-whisper]: "Can I return order PN-10004? I opened the box." (2207 ms; groq-whisper:error, local-whisper:ok) |
|  210.89s | LLM | turn 7 reply: "The BP-200 from order PN-10004 is eligible for return until 2026-10-24. Opened item within the 15-day return window with a 10% restocking fee. The final refund amount is confirmed only after the item passes inspection at our warehouse; the refund then reaches your original payment method within 5-7 business days." |
|  211.88s | ASSISTANT | turn 7 ▶ answer starts |
|  235.33s | coord | turn 7 ends: **answered** (33472 ms after EOU) |
|  235.76s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, synthetic, 3401 ms] |
|  236.23s | vad | speech start (seg 8) |
|  239.59s | vad | **end of utterance** → turn 8 (hangover 576 ms) |
|  239.59s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|  239.59s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|  240.39s | coord | turn 8 filler "one moment" (no reply after 800 ms) |
|  240.39s | ASSISTANT | turn 8 ▶ filler starts |
|  241.71s | STT | turn 8 [local-whisper]: "Hi, where is my order PN-10002?" (2122 ms; groq-whisper:error, local-whisper:ok) |
|  248.58s | LLM | turn 8 reply: "Order PN-10002 is shipped. It is with BlueDart, tracking number BD7781204IN. The carrier's estimated delivery date is 2026-10-21." |
|  249.26s | ASSISTANT | turn 8 ▶ answer starts |
|  261.93s | coord | turn 8 ends: **answered** (22342 ms after EOU) |
|  262.36s | CALLER | "What does error E2 mean on my blood pressure monitor?" [product_question, synthetic, 2841 ms] |
|  262.85s | vad | speech start (seg 9) |
|  265.60s | vad | **end of utterance** → turn 9 (hangover 576 ms) |
|  266.40s | coord | turn 9 filler "one moment" (no reply after 800 ms) |
|  266.40s | ASSISTANT | turn 9 ▶ filler starts |
|  267.63s | STT | turn 9 [local-whisper]: "What does error E2 mean on my blood pressure monitor?" (2030 ms; groq-whisper:circuit_open, local-whisper:ok) |
|  280.04s | LLM | turn 9 reply: "Error E2 on your BP-200 indicates that movement or talking was detected during the measurement [1]. To resolve this, sit with your back supported, keep your feet flat, and rest your arm at heart level on a table while staying still and quiet until the reading finishes [1]. You should then try the measurement again [1]." |
|  281.08s | ASSISTANT | turn 9 ▶ answer starts |
|  300.82s | coord | turn 9 ends: **answered** (35216 ms after EOU) |
|  301.25s | CALLER | "Can I return order PN-10004? I opened the box." [return_query, synthetic, 4413 ms] |
|  301.73s | vad | speech start (seg 10) |
|  306.05s | vad | **end of utterance** → turn 10 (hangover 576 ms) |
|  306.05s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|  306.05s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|  306.85s | coord | turn 10 filler "one moment" (no reply after 800 ms) |
|  306.85s | ASSISTANT | turn 10 ▶ filler starts |
|  308.27s | STT | turn 10 [local-whisper]: "Can I return order PN-10004? I open the box." (2217 ms; groq-whisper:error, local-whisper:ok) |
|  315.31s | LLM | turn 10 reply: "The BP-200 from order PN-10004 is eligible for return until 2026-10-24. Opened item within the 15-day return window with a 10% restocking fee. The final refund amount is confirmed only after the item passes inspection at our warehouse; the refund then reaches your original payment method within 5-7 business days." |
|  317.69s | ASSISTANT | turn 10 ▶ answer starts |
|  341.05s | coord | turn 10 ends: **answered** (34996 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1378 (filler) | 11441 | 576 | 2455 | 7558 | 852 | first audio 1378 ms (filler); answer audio 11441 ms = hangover 576 + stt 2455 + llm 7558 + tts 852 + playout 0 ms; dominated by llm (66%) |
| 2 | 1379 (filler) | 10635 | 576 | 2037 | 6958 | 1064 | first audio 1379 ms (filler); answer audio 10635 ms = hangover 576 + stt 2037 + llm 6958 + tts 1064 + playout 0 ms; dominated by llm (65%) |
| 3 | 1377 (filler) | 16431 | 576 | 2076 | 12660 | 1119 | first audio 1377 ms (filler); answer audio 16431 ms = hangover 576 + stt 2076 + llm 12660 + tts 1119 + playout 0 ms; dominated by llm (77%) |
| 4 | 1378 (filler) | 10354 | 576 | 2035 | 6958 | 784 | first audio 1378 ms (filler); answer audio 10354 ms = hangover 576 + stt 2035 + llm 6958 + tts 784 + playout 0 ms; dominated by llm (67%) |
| 5 | 1377 (filler) | 10018 | 576 | 2194 | 6406 | 842 | first audio 1377 ms (filler); answer audio 10018 ms = hangover 576 + stt 2194 + llm 6406 + tts 842 + playout 0 ms; dominated by llm (64%) |
| 6 | 1377 (filler) | 16317 | 576 | 2239 | 12466 | 1037 | first audio 1377 ms (filler); answer audio 16317 ms = hangover 576 + stt 2239 + llm 12466 + tts 1037 + playout 0 ms; dominated by llm (76%) |
| 7 | 1377 (filler) | 10600 | 576 | 2208 | 6829 | 986 | first audio 1377 ms (filler); answer audio 10600 ms = hangover 576 + stt 2208 + llm 6829 + tts 986 + playout 0 ms; dominated by llm (64%) |
| 8 | 1377 (filler) | 10251 | 576 | 2123 | 6869 | 683 | first audio 1377 ms (filler); answer audio 10251 ms = hangover 576 + stt 2123 + llm 6869 + tts 683 + playout 0 ms; dominated by llm (67%) |
| 9 | 1377 (filler) | 16053 | 576 | 2031 | 12403 | 1043 | first audio 1377 ms (filler); answer audio 16053 ms = hangover 576 + stt 2031 + llm 12403 + tts 1043 + playout 0 ms; dominated by llm (77%) |
| 10 | 1379 (filler) | 12214 | 576 | 2219 | 7045 | 2373 | first audio 1379 ms (filler); answer audio 12214 ms = hangover 576 + stt 2219 + llm 7045 + tts 2373 + playout 0 ms; dominated by llm (58%) |

- **to_first_audio**: n=10 p50=1377.3 ms p95=1378.6 ms

- **to_first_answer_audio**: n=10 p50=10635.0 ms p95=16431.4 ms

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
  "local-whisper",
  "local-whisper",
  "local-whisper",
  "local-whisper",
  "local-whisper",
  "local-whisper",
  "local-whisper",
  "local-whisper",
  "local-whisper",
  "local-whisper"
 ],
 "stt_texts": [
  "Hi, where is my order PN-10002?",
  "Can I return order PN-10004? I open the box.",
  "What does error E2 mean on my blood pressure monitor?",
  "Hi, where is my order PN-10002?",
  "I want to talk to a real person, please.",
  "What does error E2 mean on my blood pressure monitor?",
  "Can I return order PN-10004? I opened the box.",
  "Hi, where is my order PN-10002?",
  "What does error E2 mean on my blood pressure monitor?",
  "Can I return order PN-10004? I open the box."
 ],
 "fallback_used": [
  "local-whisper",
  "local-whisper",
  "local-whisper",
  "local-whisper",
  "local-whisper",
  "local-whisper",
  "local-whisper",
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
 "max_turn_wall_ms": 35436.09999999999,
 "max_time_to_resolution_ms": 15855.099999999991,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 342.98,
 "wall_s": 342.98
}
```
