# 03_real_interruption: PASS

Required #3, end to end (pipeline): 'Wait, wrong order. I meant PN-10005.' 1.5 s into the order-status answer must duck, confirm, cut playback, and become exactly one new turn that is answered. (`replies` are used only when run with --responder scripted.)

- responder: **pipeline (build_pipeline, voice)**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (real), wait_wrong_order (real)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.71s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|    1.12s | vad | speech start (seg 1) |
|    5.41s | vad | **end of utterance** → turn 1 (hangover 576 ms) |
|    6.22s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    6.22s | ASSISTANT | turn 1 ▶ filler starts |
|    6.27s | STT | turn 1 [groq-whisper]: "Hi, where is my order PN-10002?" (860 ms; groq-whisper:ok) |
|    9.64s | LLM | turn 1 reply: "Your order PN-10002 has been shipped via BlueDart express. The tracking ID is BD7781204IN, and the carrier's estimated delivery date is 2026-10-22. It was placed on 2026-10-17 and includes 1 unit of SKU SC-03." |
|   10.75s | ASSISTANT | turn 1 ▶ answer starts |
|   12.25s | CALLER | "Wait, wrong order. I meant PN-10005." [wait_wrong_order, real, 4192 ms] |
|   12.61s | coord | duck playback (possible barge-in, 160 ms voiced) |
|   12.71s | vad | speech start (seg 2) over the answer |
|   13.09s | coord | ✂ **BARGE-IN confirmed** on turn 1: cut after 2341 ms of answer audio of 23926 ms (10%) |
|   13.09s | coord | playback cancelled (barge_in) |
|   13.09s | coord | turn 1 ends: **interrupted** (7681 ms after EOU) |
|   16.96s | vad | **end of utterance** → turn 2 (hangover 576 ms, after barge-in) |
|   17.57s | STT | turn 2 [groq-whisper]: "Wait, wrong order. I mean PN-10005." (611 ms; groq-whisper:ok) |
|   17.77s | coord | turn 2 filler "one moment" (no reply after 800 ms) |
|   17.77s | ASSISTANT | turn 2 ▶ filler starts |
|   22.48s | LLM | turn 2 reply: "Order PN-10005 was placed on 2026-09-26 and has been delivered, with delivery completed on 2026-09-30. It shipped via Ecom Express standard shipping (tracking EE4410098211) and contains 1 unit of SKU OX-02. If you have any concerns about the delivery, a human agent can review it." |
|   23.83s | ASSISTANT | turn 2 ▶ answer starts |
|   53.17s | coord | turn 2 ends: **answered** (36207 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1380 (filler) | 5915 | 576 | 861 | 3366 | 1111 | first audio 1380 ms (filler); answer audio 5915 ms = hangover 576 + stt 861 + llm 3366 + tts 1111 + playout 0 ms; dominated by llm (57%) |
| 2 | 1381 (filler) | 7447 | 576 | 612 | 4902 | 1357 | first audio 1381 ms (filler); answer audio 7447 ms = hangover 576 + stt 612 + llm 4902 + tts 1357 + playout 0 ms; dominated by llm (66%) |

- **to_first_audio**: n=2 p50=1380.5 ms p95=1380.7 ms

- **to_first_answer_audio**: n=2 p50=5915.3 ms p95=7447.4 ms

## Expectations

- [x] `turns` expected 2, got 2
- [x] `barge_in_on_answer` expected 1, got 1
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
 "max_turn_wall_ms": 36205.5,
 "max_time_to_resolution_ms": 6870.200000000001,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 55.09,
 "wall_s": 55.09
}
```
