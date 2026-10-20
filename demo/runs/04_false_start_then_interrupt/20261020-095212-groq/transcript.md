# 04_false_start_then_interrupt: PASS

Adversarial (scripted): 'Uh.' 1 s into the answer must not cut it (at most a duck; Silero often scores a lone 'Uh.' below threshold in-call); 1.5 s later the real 'Wait, wrong order' must cut it. Exactly one barge-in and exactly one new turn.

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (real), false_start (real), wait_wrong_order (real)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.72s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|    1.16s | vad | speech start (seg 1) |
|    5.44s | vad | **end of utterance** → turn 1 (hangover 576 ms) |
|    6.25s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    6.25s | ASSISTANT | turn 1 ▶ filler starts |
|    6.61s | STT | turn 1 [groq-whisper]: "Hi, where is my order PN-10002?" (1164 ms; groq-whisper:ok) |
|    7.21s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|    8.67s | ASSISTANT | turn 1 ▶ answer starts |
|    9.67s | CALLER | "Uh." [false_start, real, 608 ms] |
|   10.02s | coord | duck playback (possible barge-in, 160 ms voiced) |
|   10.11s | vad | speech start (seg 2) over the answer |
|   10.50s | coord | un-duck: speech stopped before confirm, playback continues |
|   10.75s | vad | ignored overlapping speech (320 ms voiced): overlapped the answer, barge-in not confirmed |
|   11.18s | CALLER | "Wait, wrong order. I meant PN-10005." [wait_wrong_order, real, 4192 ms] |
|   11.49s | coord | duck playback (possible barge-in, 160 ms voiced) |
|   11.59s | vad | speech start (seg 3) over the answer |
|   11.94s | coord | ✂ **BARGE-IN confirmed** on turn 1: cut after 3269 ms of answer audio of 20037 ms (16%) |
|   11.94s | coord | playback cancelled (barge_in) |
|   11.94s | coord | turn 1 ends: **interrupted** (6497 ms after EOU) |
|   15.91s | vad | **end of utterance** → turn 2 (hangover 576 ms, after barge-in) |
|   16.44s | STT | turn 2 [groq-whisper]: "Wait, wrong order. I mean PN-10005." (534 ms; groq-whisper:ok) |
|   16.71s | coord | turn 2 filler "one moment" (no reply after 800 ms) |
|   16.71s | ASSISTANT | turn 2 ▶ filler starts |
|   17.04s | LLM | turn 2 reply: "Got it, order PN-10005. That's the pulse oximeter, delivered twenty days ago by Ecom Express. Is there something wrong with it?" |
|   19.13s | ASSISTANT | turn 2 ▶ answer starts |
|   30.29s | coord | turn 2 ends: **answered** (14386 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1381 (filler) | 3803 | 576 | 1165 | 600 | 1262 | first audio 1381 ms (filler); answer audio 3803 ms = hangover 576 + stt 1165 + llm 600 + tts 1262 + playout 200 ms; dominated by tts (33%) |
| 2 | 1381 (filler) | 3803 | 576 | 536 | 600 | 1048 | first audio 1381 ms (filler); answer audio 3803 ms = hangover 576 + stt 536 + llm 600 + tts 1048 + playout 1043 ms; dominated by tts (28%) |

- **to_first_audio**: n=2 p50=1380.6 ms p95=1381.4 ms

- **to_first_answer_audio**: n=2 p50=3803.3 ms p95=3803.4 ms

## Expectations

- [x] `turns` expected 2, got 2
- [x] `barge_in_confirmed` expected 1, got 1
- [x] `utterances_ignored` expected {'<=': 1}, got 1
- [x] `interrupted` expected 1, got 1
- [x] `turns_completed` expected 1, got 1
- [x] `no_duplicate_turns` expected True, got True
- [x] `no_dropped_turns` expected True, got True
- [x] `coordinator_errors` expected 0, got 0

## Counters

```json
{
 "greeting": null,
 "turns": 2,
 "turns_answered": 2,
 "turns_completed": 1,
 "interrupted": 1,
 "merged": 0,
 "outcomes": [
  "interrupted",
  "answered"
 ],
 "barge_in_confirmed": 1,
 "barge_in_on_answer": 1,
 "ducks": 2,
 "unducks": 1,
 "utterances_ignored": 1,
 "vad_rejected": 0,
 "fillers": 2,
 "stale_dropped": 0,
 "replies_held": 0,
 "canned": [],
 "stt_providers": [
  "groq-whisper",
  "groq-whisper"
 ],
 "stt_texts": [
  "Hi, where is my order PN-10002?",
  "Wait, wrong order. I mean PN-10005."
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
 "max_turn_wall_ms": 14384.300000000001,
 "max_time_to_resolution_ms": 3226.199999999999,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 32.2,
 "wall_s": 32.2
}
```
