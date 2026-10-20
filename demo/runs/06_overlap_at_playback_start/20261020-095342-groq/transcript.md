# 06_overlap_at_playback_start: PASS

Adversarial (scripted): (a) the caller starts 'Wait, wrong order' 150 ms after the answer starts; (b) on the next question the caller starts talking the moment the reply is ready, before its first audio. Both must resolve to one barge-in or one merge, never two answers in parallel.

- responder: **scripted**; providers: real ['groq-whisper', 'local-whisper'] / ['inhouse', 'edge']; VAD: silero
- clips: order_status_query (real), wait_wrong_order (real), return_query (real)
- barge-in: duck 160 ms, confirm 560 ms in 1200 ms window; endpointer: min speech 250 ms, hangover 560 ms; filler after 800 ms

## Timeline

| t | who | what |
|---|---|---|
|    0.70s | CALLER | "Hi, where is my order PN-10002?" [order_status_query, real, 4256 ms] |
|    1.12s | vad | speech start (seg 1) |
|    5.41s | vad | **end of utterance** → turn 1 (hangover 576 ms) |
|    6.04s | STT | turn 1 [groq-whisper]: "Hi, where is my order PN-10002?" (628 ms; groq-whisper:ok) |
|    6.21s | coord | turn 1 filler "one moment" (no reply after 800 ms) |
|    6.21s | ASSISTANT | turn 1 ▶ filler starts |
|    6.64s | LLM | turn 1 reply: "Your order PN-10002, the smart scale, shipped three days ago by BlueDart express. The tracking number is B D 7 7 8 1 2 0 4 I N. BlueDart expects to deliver it in about two days. You'll get an SMS from the courier on the day of delivery." |
|    8.64s | ASSISTANT | turn 1 ▶ answer starts |
|    8.79s | CALLER | "Wait, wrong order. I meant PN-10005." [wait_wrong_order, real, 4192 ms] |
|    9.12s | coord | duck playback (possible barge-in, 160 ms voiced) |
|    9.22s | vad | speech start (seg 2) over the answer |
|    9.57s | coord | ✂ **BARGE-IN confirmed** on turn 1: cut after 932 ms of answer audio of 20147 ms (5%) |
|    9.57s | coord | playback cancelled (barge_in) |
|    9.57s | coord | turn 1 ends: **interrupted** (4161 ms after EOU) |
|   13.51s | vad | **end of utterance** → turn 2 (hangover 576 ms, after barge-in) |
|   14.16s | STT | turn 2 [groq-whisper]: "Wait, wrong order. I mean PN-10005." (655 ms; groq-whisper:ok) |
|   14.31s | coord | turn 2 filler "one moment" (no reply after 800 ms) |
|   14.31s | ASSISTANT | turn 2 ▶ filler starts |
|   14.76s | LLM | turn 2 reply: "Got it, order PN-10005. That's the pulse oximeter, delivered twenty days ago by Ecom Express. Is there something wrong with it?" |
|   16.73s | ASSISTANT | turn 2 ▶ answer starts |
|   27.84s | coord | turn 2 ends: **answered** (14336 ms after EOU) |
|   28.27s | CALLER | "Can I return order PN-10004? I opened the box." [return_query, real, 5664 ms] |
|   28.77s | vad | speech start (seg 3) |
|   34.40s | vad | **end of utterance** → turn 3 (hangover 576 ms) |
|   35.09s | STT | turn 3 [groq-whisper]: "Can I return order PN-10004? I have opened the box." (684 ms; groq-whisper:ok) |
|   35.20s | coord | turn 3 filler "one moment" (no reply after 800 ms) |
|   35.20s | ASSISTANT | turn 3 ▶ filler starts |
|   35.69s | LLM | turn 3 reply: "Order PN-10004 is a blood pressure monitor delivered ten days ago. Since the box was opened, it can still be returned within the window, with a restocking fee. Would you like me to start the return?" |
|   35.69s | CALLER | "Wait, wrong order. I meant PN-10005." [wait_wrong_order, real, 4192 ms] |
|   36.00s | coord | duck playback (possible barge-in, 160 ms voiced) |
|   36.10s | vad | speech start (seg 4) |
|   36.45s | coord | ✂ **BARGE-IN confirmed** on turn 3: cut after None ms of filler |
|   36.45s | coord | playback cancelled (barge_in) |
|   40.39s | coord | turn 4 merges cancelled turn 3 (was speaking; carried "Can I return order PN-10004? I have opened the box." + 0 ms audio) |
|   40.39s | coord | turn 3 ends: **merged** (5985 ms after EOU) |
|   40.39s | vad | **end of utterance** → turn 4 (hangover 576 ms, after barge-in, merged from turns [3]) |
|   41.05s | STT | turn 4 [groq-whisper]: "Can I return order PN-10004? I have opened the box. Wait, wrong order. I mean PN-10005." (661 ms; groq-whisper:ok) |
|   41.19s | coord | turn 4 filler "one moment" (no reply after 800 ms) |
|   41.19s | ASSISTANT | turn 4 ▶ filler starts |
|   41.65s | LLM | turn 4 reply: "Got it, order PN-10005. That's the pulse oximeter, delivered twenty days ago by Ecom Express. Is there something wrong with it?" |
|   43.61s | ASSISTANT | turn 4 ▶ answer starts |
|   54.80s | coord | turn 4 ends: **answered** (14415 ms after EOU) |

## Latency (end of caller speech → assistant audio)

| turn | first audio | first answer audio | hangover | STT | LLM | TTS 1st chunk | note |
|---|---|---|---|---|---|---|---|
| 1 | 1381 (filler) | 3804 | 576 | 630 | 601 | 1156 | first audio 1381 ms (filler); answer audio 3804 ms = hangover 576 + stt 630 + llm 601 + tts 1156 + playout 840 ms; dominated by tts (30%) |
| 2 | 1380 (filler) | 3802 | 576 | 656 | 601 | 1103 | first audio 1380 ms (filler); answer audio 3802 ms = hangover 576 + stt 656 + llm 601 + tts 1103 + playout 866 ms; dominated by tts (29%) |
| 3 | 1379 (filler) | - | 576 | 685 | 600 | - | merged |
| 4 | 1380 (filler) | 3802 | 576 | 663 | 601 | 1380 | first audio 1380 ms (filler); answer audio 3802 ms = hangover 576 + stt 663 + llm 601 + tts 1380 + playout 582 ms; dominated by tts (36%) |

- **to_first_audio**: n=3 p50=1380.1 ms p95=1380.9 ms

- **to_first_answer_audio**: n=3 p50=3802.4 ms p95=3803.6 ms

## Expectations

- [x] `barge_in_confirmed` expected {'>=': 2}, got 2
- [x] `turns_answered` expected {'>=': 3}, got 3
- [x] `no_duplicate_turns` expected True, got True
- [x] `no_dropped_turns` expected True, got True
- [x] `coordinator_errors` expected 0, got 0

## Counters

```json
{
 "greeting": null,
 "turns": 4,
 "turns_answered": 3,
 "turns_completed": 2,
 "interrupted": 1,
 "merged": 1,
 "outcomes": [
  "interrupted",
  "answered",
  "merged",
  "answered"
 ],
 "barge_in_confirmed": 2,
 "barge_in_on_answer": 1,
 "ducks": 2,
 "unducks": 0,
 "utterances_ignored": 0,
 "vad_rejected": 0,
 "fillers": 4,
 "stale_dropped": 0,
 "replies_held": 0,
 "canned": [],
 "stt_providers": [
  "groq-whisper",
  "groq-whisper",
  "groq-whisper",
  "groq-whisper"
 ],
 "stt_texts": [
  "Hi, where is my order PN-10002?",
  "Wait, wrong order. I mean PN-10005.",
  "Can I return order PN-10004? I have opened the box.",
  "Can I return order PN-10004? I have opened the box. Wait, wrong order. I mean PN-10005."
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
 "max_turn_wall_ms": 14413.700000000004,
 "max_time_to_resolution_ms": 5984.399999999994,
 "coordinator_errors": 0,
 "invariant_violations": 0,
 "timed_out": false,
 "runner_error": [],
 "call_s": 56.74,
 "wall_s": 56.74
}
```
