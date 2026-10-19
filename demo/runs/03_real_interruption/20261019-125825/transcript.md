# 03_real_interruption: PASS

Required #3, end to end (pipeline): 'Wait, wrong order. I meant PN-10005.' 1.5 s into the order-status answer must duck, confirm, cut playback, and become exactly one new turn that is answered. (`replies` are used only when run with --responder scripted.)

- responder: **pipeline (build_pipeline, voice)**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (SYNTHETIC), wait_wrong_order (SYNTHETIC)
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
|    6.78s | STT | turn 1 [local-whisper]: "Hi, where is my order PN-10002?" (2231 ms; groq-whisper:error, local-whisper:ok) |
|   14.70s | LLM | turn 1 reply: "Order PN-10002 is shipped. It is with BlueDart, tracking number BD7781204IN. The carrier's estimated delivery date is 2026-10-21." |
|   15.55s | ASSISTANT | turn 1 ▶ answer starts |
|   17.05s | CALLER | "Wait, wrong order. I meant PN-10005." [wait_wrong_order, synthetic, 3541 ms] |
|   17.41s | coord | duck playback (possible barge-in, 160 ms voiced) |
|   17.51s | vad | speech start (seg 2) over the answer |
|   17.83s | coord | ✂ **BARGE-IN confirmed** on turn 1: cut after 2278 ms of answer audio of 14276 ms (16%) |
|   17.83s | coord | playback cancelled (barge_in) |
|   17.83s | coord | turn 1 ends: **interrupted** (13281 ms after EOU) |
|   21.03s | vad | **end of utterance** → turn 2 (hangover 576 ms, after barge-in) |
|   21.03s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|   21.03s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|   21.83s | coord | turn 2 filler "one moment" (no reply after 800 ms) |
|   21.83s | ASSISTANT | turn 2 ▶ filler starts |
|   22.99s | STT | turn 2 [local-whisper]: "Wait, wrong order, I meant PN-10005." (1966 ms; groq-whisper:error, local-whisper:ok) |
|   30.21s | LLM | turn 2 reply: "Order PN-10005 is delivered. It is with Ecom Express, tracking number EE4410098211. It was delivered on 2026-09-29." |
|   31.16s | ASSISTANT | turn 2 ▶ answer starts |
|   44.80s | coord | turn 2 ends: **answered** (23777 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1377 (filler) | 11578 | 576 | 2232 | 7927 | 844 | first audio 1377 ms (filler); answer audio 11578 ms = hangover 576 + stt 2232 + llm 7927 + tts 844 + playout 0 ms; dominated by llm (68%) |
| 2 | 1378 (filler) | 10714 | 576 | 1967 | 7213 | 957 | first audio 1378 ms (filler); answer audio 10714 ms = hangover 576 + stt 1967 + llm 7213 + tts 957 + playout 0 ms; dominated by llm (67%) |

- **to_first_audio**: n=2 p50=1376.8 ms p95=1377.8 ms

- **to_first_answer_audio**: n=2 p50=10714.3 ms p95=11578.0 ms

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
  "local-whisper",
  "local-whisper"
 ],
 "stt_texts": [
  "Hi, where is my order PN-10002?",
  "Wait, wrong order, I meant PN-10005."
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
 "max_turn_wall_ms": 23775.800000000003,
 "max_time_to_resolution_ms": 11001.900000000001,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 46.72,
 "wall_s": 46.72
}
```
