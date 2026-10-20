# 02_noise_burst_no_interrupt: PASS

Required #2 (scripted responder: isolates barge-in from LLM latency): a 120 ms clap 1.2 s into the answer must not duck or interrupt it.

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (real), noise_burst (real)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.73s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|    1.16s | vad | speech start (seg 1) |
|    5.45s | vad | **end of utterance** → turn 1 (hangover 576 ms) |
|    6.07s | STT | turn 1 [groq-whisper]: "Hi, where is my order PN-10002?" (622 ms; groq-whisper:ok) |
|    6.25s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    6.25s | ASSISTANT | turn 1 ▶ filler starts |
|    6.67s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|    8.68s | ASSISTANT | turn 1 ▶ answer starts |
|    9.88s | CALLER | (one sharp clap or a desk knock, ~0.1 s) [noise_burst, real, 2000 ms] |
|   28.72s | coord | turn 1 ends: **answered** (23269 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1382 (filler) | 3805 | 576 | 625 | 601 | 1154 | first audio 1382 ms (filler); answer audio 3805 ms = hangover 576 + stt 625 + llm 601 + tts 1154 + playout 849 ms; dominated by tts (30%) |

- **to_first_audio**: n=1 p50=1381.9 ms p95=1381.9 ms

- **to_first_answer_audio**: n=1 p50=3804.6 ms p95=3804.6 ms

## Expectations

- [x] `turns` expected 1, got 1
- [x] `turns_completed` expected 1, got 1
- [x] `barge_in_confirmed` expected 0, got 0
- [x] `interrupted` expected 0, got 0
- [x] `no_duplicate_turns` expected True, got True
- [x] `no_dropped_turns` expected True, got True
- [x] `coordinator_errors` expected 0, got 0

## Counters

```json
{
 "greeting": null,
 "turns": 1,
 "turns_answered": 1,
 "turns_completed": 1,
 "interrupted": 0,
 "merged": 0,
 "outcomes": [
  "answered"
 ],
 "barge_in_confirmed": 0,
 "barge_in_on_answer": 0,
 "ducks": 0,
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
 "max_turn_wall_ms": 23267.0,
 "max_time_to_resolution_ms": 3226.4000000000005,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 30.64,
 "wall_s": 30.64
}
```
