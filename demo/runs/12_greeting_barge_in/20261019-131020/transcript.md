# 12_greeting_barge_in: PASS

Greeting hook (scripted responder with a `greeting`): the assistant greets as turn 0 on connect; the caller starts 'Hi, where is my order' 0.8 s into the greeting, which must barge in and become turn 1 (greeting interrupted, never merged into the caller's text, no duplicate turn).

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (SYNTHETIC)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.10s | ASSISTANT | turn 0 greeting: "Hello, thanks for calling PulseNest support. You can ask about an order, a return, or any of our devices. How can I help you today?" |
|    0.92s | ASSISTANT | turn 0 ▶ answer starts |
|    1.72s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, synthetic, 3401 ms] |
|    2.08s | coord | duck playback (possible barge-in, 160 ms voiced) |
|    2.18s | vad | speech start (seg 1) over the answer |
|    2.50s | coord | ✂ **BARGE-IN confirmed** on turn 0: cut after 1582 ms of answer audio of 8846 ms (18%) |
|    2.50s | coord | playback cancelled (barge_in) |
|    2.50s | coord | turn 0 ends: **interrupted** (2398 ms after EOU) |
|    5.54s | vad | **end of utterance** → turn 1 (hangover 576 ms, after barge-in) |
|    5.54s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|    5.54s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|    6.34s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    6.34s | ASSISTANT | turn 1 ▶ filler starts |
|    7.56s | STT | turn 1 [local-whisper]: "Hi, where is my order PN-10002?" (2023 ms; groq-whisper:error, local-whisper:ok) |
|    8.16s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|    9.07s | ASSISTANT | turn 1 ▶ answer starts |
|   26.98s | coord | turn 1 ends: **answered** (21443 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 0 | - (answer) | - | - | - | - | - | greeting |
| 1 | 1379 (filler) | 4108 | 576 | 2025 | 601 | 907 | first audio 1379 ms (filler); answer audio 4108 ms = hangover 576 + stt 2025 + llm 601 + tts 907 + playout 0 ms; dominated by stt (49%) |

- **to_first_audio**: n=1 p50=1379.1 ms p95=1379.1 ms

- **to_first_answer_audio**: n=1 p50=4108.2 ms p95=4108.2 ms

## Expectations

- [x] `greeting` expected 'interrupted', got 'interrupted'
- [x] `turns` expected 1, got 1
- [x] `turns_completed` expected 1, got 1
- [x] `barge_in_confirmed` expected 1, got 1
- [x] `no_duplicate_turns` expected True, got True
- [x] `no_dropped_turns` expected True, got True
- [x] `coordinator_errors` expected 0, got 0

## Counters

```json
{
 "greeting": "interrupted",
 "turns": 1,
 "turns_answered": 1,
 "turns_completed": 1,
 "interrupted": 0,
 "merged": 0,
 "outcomes": [
  "answered"
 ],
 "barge_in_confirmed": 1,
 "barge_in_on_answer": 1,
 "ducks": 1,
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
 "max_turn_wall_ms": 21441.699999999997,
 "max_time_to_resolution_ms": 3530.800000000001,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 28.92,
 "wall_s": 28.92
}
```
