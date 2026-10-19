# 05_backchannel: PASS

Adversarial (scripted): 'Uh-huh.' 2 s into the answer. Chosen behaviour: a backchannel is acknowledgement, not a turn; playback ducks briefly and resumes, no new turn, the answer completes.

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (SYNTHETIC), backchannel (SYNTHETIC)
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
|    6.20s | STT | turn 1 [local-whisper]: "Hi, where is my order PN-10002?" (1651 ms; groq-whisper:error, local-whisper:ok) |
|    6.80s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|    7.84s | ASSISTANT | turn 1 ▶ answer starts |
|    9.84s | CALLER | "Uh-huh." [backchannel, synthetic, 801 ms] |
|   10.21s | coord | duck playback (possible barge-in, 160 ms voiced) |
|   10.31s | vad | speech start (seg 2) over the answer |
|   10.79s | coord | un-duck: speech stopped before confirm, playback continues |
|   11.04s | vad | ignored overlapping speech (416 ms voiced): overlapped the answer, barge-in not confirmed |
|   25.91s | coord | turn 1 ends: **answered** (21365 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1378 (filler) | 3870 | 576 | 1652 | 601 | 1040 | first audio 1378 ms (filler); answer audio 3870 ms = hangover 576 + stt 1652 + llm 601 + tts 1040 + playout 0 ms; dominated by stt (43%) |

- **to_first_audio**: n=1 p50=1378.0 ms p95=1378.0 ms

- **to_first_answer_audio**: n=1 p50=3870.2 ms p95=3870.2 ms

## Expectations

- [x] `turns` expected 1, got 1
- [x] `turns_completed` expected 1, got 1
- [x] `barge_in_confirmed` expected 0, got 0
- [x] `utterances_ignored` expected 1, got 1
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
 "ducks": 1,
 "unducks": 1,
 "utterances_ignored": 1,
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
 "max_turn_wall_ms": 21363.6,
 "max_time_to_resolution_ms": 3293.5,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 27.84,
 "wall_s": 27.84
}
```
