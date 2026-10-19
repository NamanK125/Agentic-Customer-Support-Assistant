# 04_false_start_then_interrupt: PASS

Adversarial (scripted): 'Uh.' 1 s into the answer must not cut it (at most a duck; Silero often scores a lone 'Uh.' below threshold in-call); 1.5 s later the real 'Wait, wrong order' must cut it. Exactly one barge-in and exactly one new turn.

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (SYNTHETIC), false_start (SYNTHETIC), wait_wrong_order (SYNTHETIC)
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
|    6.53s | STT | turn 1 [local-whisper]: "Hi, where is my order PN-10002?" (1987 ms; groq-whisper:error, local-whisper:ok) |
|    7.13s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|    8.18s | ASSISTANT | turn 1 ▶ answer starts |
|    9.18s | CALLER | "Uh." [false_start, synthetic, 201 ms] |
|   10.69s | CALLER | "Wait, wrong order. I meant PN-10005." [wait_wrong_order, synthetic, 3541 ms] |
|   11.04s | coord | duck playback (possible barge-in, 160 ms voiced) |
|   11.14s | vad | speech start (seg 2) over the answer |
|   11.46s | coord | ✂ **BARGE-IN confirmed** on turn 1: cut after 3276 ms of answer audio of 17727 ms (18%) |
|   11.46s | coord | playback cancelled (barge_in) |
|   11.46s | coord | turn 1 ends: **interrupted** (6912 ms after EOU) |
|   14.66s | vad | **end of utterance** → turn 2 (hangover 576 ms, after barge-in) |
|   14.66s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|   14.66s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|   15.46s | coord | turn 2 filler "one moment" (no reply after 800 ms) |
|   15.46s | ASSISTANT | turn 2 ▶ filler starts |
|   16.57s | STT | turn 2 [local-whisper]: "Wait, wrong order. I meant PN-10005." (1912 ms; groq-whisper:error, local-whisper:ok) |
|   17.17s | LLM | turn 2 reply: "Got it, order PN-10005. That's the pulse oximeter, delivered twenty days ago by Ecom Express. Is there something wrong with it?" |
|   18.13s | ASSISTANT | turn 2 ▶ answer starts |
|   27.69s | coord | turn 2 ends: **answered** (13034 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1378 (filler) | 4212 | 576 | 1988 | 601 | 1046 | first audio 1378 ms (filler); answer audio 4212 ms = hangover 576 + stt 1988 + llm 601 + tts 1046 + playout 0 ms; dominated by stt (47%) |
| 2 | 1377 (filler) | 4052 | 576 | 1912 | 601 | 962 | first audio 1377 ms (filler); answer audio 4052 ms = hangover 576 + stt 1912 + llm 601 + tts 962 + playout 0 ms; dominated by stt (47%) |

- **to_first_audio**: n=2 p50=1376.7 ms p95=1377.6 ms

- **to_first_answer_audio**: n=2 p50=4051.9 ms p95=4211.7 ms

## Expectations

- [x] `turns` expected 2, got 2
- [x] `barge_in_confirmed` expected 1, got 1
- [x] `utterances_ignored` expected {'<=': 1}, got 0
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
 "ducks": 1,
 "unducks": 0,
 "utterances_ignored": 0,
 "vad_rejected": 0,
 "fillers": 2,
 "stale_dropped": 0,
 "replies_held": 0,
 "canned": [],
 "stt_providers": [
  "local-whisper",
  "local-whisper"
 ],
 "stt_texts": [
  "Hi, where is my order PN-10002?",
  "Wait, wrong order. I meant PN-10005."
 ],
 "fallback_used": [
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
 "max_turn_wall_ms": 13034.300000000001,
 "max_time_to_resolution_ms": 3635.1000000000004,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 29.63,
 "wall_s": 29.63
}
```
