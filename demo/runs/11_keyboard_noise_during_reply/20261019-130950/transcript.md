# 11_keyboard_noise_during_reply: PASS

Adversarial (scripted): steady background hum/hiss for the whole call, plus 1.2 s of keyboard clicks 0.8 s into the answer. Neither may interrupt.

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: background_noise (SYNTHETIC), order_status_query (SYNTHETIC), keyboard_clicks (SYNTHETIC)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.72s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, synthetic, 3401 ms] |
|    1.19s | vad | speech start (seg 1) |
|    4.61s | vad | **end of utterance** → turn 1 (hangover 576 ms) |
|    4.61s | provider | stt groq-whisper error: groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY) |
|    4.61s | provider | stt fallback groq-whisper → local-whisper (groq-whisper: no API key configured (STT_API_KEY / LLM_API_KEY)) |
|    5.41s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    5.41s | ASSISTANT | turn 1 ▶ filler starts |
|    6.63s | STT | turn 1 [local-whisper]: "Hi, where is my order PN-10002?" (2016 ms; groq-whisper:error, local-whisper:ok) |
|    7.23s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|    8.16s | ASSISTANT | turn 1 ▶ answer starts |
|    8.96s | CALLER | (typing on a keyboard near the mic, ~1-2 s) [keyboard_clicks, synthetic, 1200 ms] |
|   25.90s | coord | turn 1 ends: **answered** (21288 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1377 (filler) | 4123 | 576 | 2017 | 601 | 929 | first audio 1377 ms (filler); answer audio 4123 ms = hangover 576 + stt 2017 + llm 601 + tts 929 + playout 0 ms; dominated by stt (49%) |

- **to_first_audio**: n=1 p50=1377.4 ms p95=1377.4 ms

- **to_first_answer_audio**: n=1 p50=4122.6 ms p95=4122.6 ms

## Expectations

- [x] `turns` expected 1, got 1
- [x] `turns_completed` expected 1, got 1
- [x] `barge_in_confirmed` expected 0, got 0
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
 "max_turn_wall_ms": 21287.6,
 "max_time_to_resolution_ms": 3546.3,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 27.83,
 "wall_s": 27.83
}
```
