# 05_backchannel: PASS

Adversarial (scripted): 'Uh-huh.' 2 s into the answer. Chosen behaviour: a backchannel is acknowledgement, not a turn; playback ducks briefly and resumes, no new turn, the answer completes.

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (real), backchannel (real)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.72s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|    1.16s | vad | speech start (seg 1) |
|    5.44s | vad | **end of utterance** → turn 1 (hangover 576 ms) |
|    6.25s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    6.25s | ASSISTANT | turn 1 ▶ filler starts |
|    6.56s | STT | turn 1 [groq-whisper]: "Hi, where is my order PN-10002?" (1117 ms; groq-whisper:ok) |
|    7.16s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|    8.67s | ASSISTANT | turn 1 ▶ answer starts |
|   10.67s | CALLER | "Uh-huh." [backchannel, real, 864 ms] |
|   11.04s | coord | duck playback (possible barge-in, 160 ms voiced) |
|   11.14s | vad | speech start (seg 2) over the answer |
|   11.75s | coord | un-duck: speech stopped before confirm, playback continues |
|   12.00s | vad | ignored overlapping speech (544 ms voiced): overlapped the answer, barge-in not confirmed |
|   28.70s | coord | turn 1 ends: **answered** (23257 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1380 (filler) | 3803 | 576 | 1119 | 601 | 1240 | first audio 1380 ms (filler); answer audio 3803 ms = hangover 576 + stt 1119 + llm 601 + tts 1240 + playout 267 ms; dominated by tts (33%) |

- **to_first_audio**: n=1 p50=1380.4 ms p95=1380.4 ms

- **to_first_answer_audio**: n=1 p50=3803.3 ms p95=3803.3 ms

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
 "max_turn_wall_ms": 23256.300000000003,
 "max_time_to_resolution_ms": 3226.2999999999993,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 30.63,
 "wall_s": 30.63
}
```
