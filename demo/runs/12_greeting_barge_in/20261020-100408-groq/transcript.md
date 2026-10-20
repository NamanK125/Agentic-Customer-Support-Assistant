# 12_greeting_barge_in: PASS

Greeting hook (scripted responder with a `greeting`): the assistant greets as turn 0 on connect; the caller starts 'Hi, where is my order' 0.8 s into the greeting, which must barge in and become turn 1 (greeting interrupted, never merged into the caller's text, no duplicate turn).

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (real)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.10s | ASSISTANT | turn 0 greeting: "Hello, thanks for calling PulseNest support. You can ask about an order, a return, or any of our devices. How can I help you today?" |
|    1.32s | ASSISTANT | turn 0 ▶ answer starts |
|    2.12s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|    2.47s | coord | duck playback (possible barge-in, 160 ms voiced) |
|    2.56s | vad | speech start (seg 1) over the answer |
|    2.88s | coord | ✂ **BARGE-IN confirmed** on turn 0: cut after 1560 ms of answer audio of 10306 ms (15%) |
|    2.88s | coord | playback cancelled (barge_in) |
|    2.88s | coord | turn 0 ends: **interrupted** (2781 ms after EOU) |
|    6.85s | vad | **end of utterance** → turn 1 (hangover 576 ms, after barge-in) |
|    7.42s | STT | turn 1 [groq-whisper]: "Hi, where is my order PN-10002?" (572 ms; groq-whisper:ok) |
|    7.65s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    7.66s | ASSISTANT | turn 1 ▶ filler starts |
|    8.02s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|   10.08s | ASSISTANT | turn 1 ▶ answer starts |
|   30.18s | coord | turn 1 ends: **answered** (23329 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 0 | - (answer) | - | - | - | - | - | greeting |
| 1 | 1382 (filler) | 3804 | 576 | 573 | 601 | 1195 | first audio 1382 ms (filler); answer audio 3804 ms = hangover 576 + stt 573 + llm 601 + tts 1195 + playout 859 ms; dominated by tts (31%) |

- **to_first_audio**: n=1 p50=1381.6 ms p95=1381.6 ms

- **to_first_answer_audio**: n=1 p50=3804.3 ms p95=3804.3 ms

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
 "max_turn_wall_ms": 23328.6,
 "max_time_to_resolution_ms": 3227.800000000001,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 32.1,
 "wall_s": 32.1
}
```
