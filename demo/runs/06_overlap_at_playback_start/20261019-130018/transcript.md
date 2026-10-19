# 06_overlap_at_playback_start: PASS

Adversarial (scripted): (a) the caller starts 'Wait, wrong order' 150 ms after the answer starts; (b) on the next question the caller starts talking the moment the reply is ready, before its first audio. Both must resolve to one barge-in or one merge, never two answers in parallel.

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (SYNTHETIC), wait_wrong_order (SYNTHETIC), return_query (SYNTHETIC)
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
|    6.48s | STT | turn 1 [local-whisper]: "Hi, where is my order PN-10002?" (1928 ms; groq-whisper:error, local-whisper:ok) |
|    7.08s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|    8.14s | ASSISTANT | turn 1 ▶ answer starts |
|    8.29s | CALLER | "Wait, wrong order. I meant PN-10005." [wait_wrong_order, synthetic, 3541 ms] |
|    8.64s | coord | duck playback (possible barge-in, 160 ms voiced) |
|    8.74s | vad | speech start (seg 2) over the answer |
|    9.06s | coord | ✂ **BARGE-IN confirmed** on turn 1: cut after 922 ms of answer audio of 17607 ms (5%) |
|    9.06s | coord | playback cancelled (barge_in) |
|    9.06s | coord | turn 1 ends: **interrupted** (4514 ms after EOU) |
|   12.26s | vad | **end of utterance** → turn 2 (hangover 576 ms, after barge-in) |
|   12.26s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|   12.26s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|   13.06s | coord | turn 2 filler "one moment" (no reply after 800 ms) |
|   13.06s | ASSISTANT | turn 2 ▶ filler starts |
|   14.32s | STT | turn 2 [local-whisper]: "Wait, wrong order. I meant PN-10005." (2061 ms; groq-whisper:error, local-whisper:ok) |
|   14.92s | LLM | turn 2 reply: "Got it, order PN-10005. That's the pulse oximeter, delivered twenty days ago by Ecom Express. Is there something wrong with it?" |
|   15.78s | ASSISTANT | turn 2 ▶ answer starts |
|   25.28s | coord | turn 2 ends: **answered** (13020 ms after EOU) |
|   25.72s | CALLER | "Can I return order PN-10004? I opened the box." [return_query, synthetic, 4413 ms] |
|   26.21s | vad | speech start (seg 3) |
|   30.53s | vad | **end of utterance** → turn 3 (hangover 576 ms) |
|   30.53s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|   30.53s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|   31.33s | coord | turn 3 filler "one moment" (no reply after 800 ms) |
|   31.33s | ASSISTANT | turn 3 ▶ filler starts |
|   32.50s | STT | turn 3 [local-whisper]: "Can I return order PN-10004? I open the box." (1969 ms; groq-whisper:error, local-whisper:ok) |
|   33.10s | LLM | turn 3 reply: "Order PN-10004 is a blood pressure monitor delivered ten days ago. Since the box was opened, it can still be returned within the window, with a restocking fee. Would you like me to start the return?" |
|   33.10s | CALLER | "Wait, wrong order. I meant PN-10005." [wait_wrong_order, synthetic, 3541 ms] |
|   33.54s | vad | speech start (seg 4) |
|   34.27s | ASSISTANT | turn 3 ▶ answer starts |
|   34.47s | coord | duck playback (possible barge-in, 160 ms voiced) |
|   34.88s | coord | ✂ **BARGE-IN confirmed** on turn 3: cut after 612 ms of answer audio of 13367 ms (5%) |
|   34.88s | coord | playback cancelled (barge_in) |
|   34.88s | coord | turn 3 ends: **interrupted** (4352 ms after EOU) |
|   37.09s | vad | **end of utterance** → turn 4 (hangover 576 ms, after barge-in) |
|   37.89s | coord | turn 4 filler "one moment" (no reply after 800 ms) |
|   37.89s | ASSISTANT | turn 4 ▶ filler starts |
|   39.06s | STT | turn 4 [local-whisper]: "Wait, wrong order. I meant PN-10005." (1971 ms; groq-whisper:circuit_open, local-whisper:ok) |
|   39.66s | LLM | turn 4 reply: "Got it, order PN-10005. That's the pulse oximeter, delivered twenty days ago by Ecom Express. Is there something wrong with it?" |
|   40.62s | ASSISTANT | turn 4 ▶ answer starts |
|   50.39s | coord | turn 4 ends: **answered** (13302 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1378 (filler) | 4166 | 576 | 1930 | 600 | 1059 | first audio 1378 ms (filler); answer audio 4166 ms = hangover 576 + stt 1930 + llm 600 + tts 1059 + playout 0 ms; dominated by stt (46%) |
| 2 | 1378 (filler) | 4098 | 576 | 2063 | 601 | 858 | first audio 1378 ms (filler); answer audio 4098 ms = hangover 576 + stt 2063 + llm 601 + tts 858 + playout 0 ms; dominated by stt (50%) |
| 3 | 1378 (filler) | 4316 | 576 | 1970 | 601 | 1168 | first audio 1378 ms (filler); answer audio 4316 ms = hangover 576 + stt 1970 + llm 601 + tts 1168 + playout 0 ms; dominated by stt (46%) |
| 4 | 1376 (filler) | 4111 | 576 | 1971 | 600 | 963 | first audio 1376 ms (filler); answer audio 4111 ms = hangover 576 + stt 1971 + llm 600 + tts 963 + playout 0 ms; dominated by stt (48%) |

- **to_first_audio**: n=4 p50=1378.1 ms p95=1378.2 ms

- **to_first_answer_audio**: n=4 p50=4110.7 ms p95=4315.7 ms

## Expectations

- [x] `barge_in_confirmed` expected {'>=': 2}, got 2
- [x] `turns_answered` expected {'>=': 3}, got 4
- [x] `no_duplicate_turns` expected True, got True
- [x] `no_dropped_turns` expected True, got True
- [x] `coordinator_errors` expected 0, got 0

## Counters

```json
{
 "greeting": null,
 "turns": 4,
 "turns_answered": 4,
 "turns_completed": 2,
 "interrupted": 2,
 "merged": 0,
 "outcomes": [
  "interrupted",
  "answered",
  "interrupted",
  "answered"
 ],
 "barge_in_confirmed": 2,
 "barge_in_on_answer": 2,
 "ducks": 2,
 "unducks": 0,
 "utterances_ignored": 0,
 "vad_rejected": 0,
 "fillers": 4,
 "stale_dropped": 0,
 "replies_held": 0,
 "canned": [],
 "stt_providers": [
  "local-whisper",
  "local-whisper",
  "local-whisper",
  "local-whisper"
 ],
 "stt_texts": [
  "Hi, where is my order PN-10002?",
  "Wait, wrong order. I meant PN-10005.",
  "Can I return order PN-10004? I open the box.",
  "Wait, wrong order. I meant PN-10005."
 ],
 "fallback_used": [
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
 "max_turn_wall_ms": 13302.5,
 "max_time_to_resolution_ms": 3738.5,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 52.33,
 "wall_s": 52.33
}
```
