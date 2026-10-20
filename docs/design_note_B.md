# Design note B: voice turn-taking

Scope: the phone-line channel in `src/support_assistant/voice/`. The pipeline is caller audio →
VAD/endpointer → STT → the text pipeline from Part A (`channel="voice"`, so the same guards and
router) → sentence-streamed TTS → caller. Measured numbers are in §5. Failure cases we found and
did not fix are in §6.

## 1. Detecting end of utterance robustly across accents and languages

What we ship is the simplest thing that is honest about its limits: a neural VAD (Silero, 32 ms
frames) feeding an endpointer that declares end-of-utterance after a fixed **silence hangover**,
with a pre-roll buffer so the first syllable is not clipped. A fixed hangover is the weak point.
It encodes one speaker's pause pattern:

- **Pause length is language- and speaker-dependent.** Hindi–English code-switching callers
  often pause mid-clause while searching for an English word. Slow or elderly speakers (a real
  share of callers for a home BP monitor) pause longer. Reading out an order number ("P N, one
  zero, zero zero five") produces long inter-digit gaps that are not turn ends.
- **A short hangover cuts people off; a long one adds latency to every turn.** Hangover is paid
  in full on every turn, before STT even starts (see §5), so it is the cheapest latency to win
  and the most dangerous to win carelessly.

How we would make it robust, in the order we would do it:

1. **Make the hangover adaptive, not global.** Track each caller's own inter-word pause
   distribution during the call (VAD gives it for free) and set the hangover at roughly their p90
   pause plus a margin, clamped to [400, 1200] ms. This handles slow speakers and code-switchers
   without slowing everyone down.
2. **Context-dependent hangover.** When the assistant has just asked for something that comes in
   pieces (an order id, a phone number, an address), extend the hangover and end early only when
   the expected pattern completes (`PN-` + 5 digits). The dialogue state knows what it asked for.
   VAD alone never will.
3. **Semantic / prosodic end-of-turn model.** Combine acoustic cues (falling pitch, final
   lengthening, energy decay) with a text signal from a streaming partial transcript ("is this a
   complete question?"). A small turn-detection model over the partial transcript is now standard
   practice. It lets the system answer quickly after "Where is my order PN-10002?" while waiting
   after "I wanted to ask about, um…". This needs streaming ASR (§3).
4. **Evaluate per language/accent, not in aggregate.** The metric pair is *false cut-off rate*
   (we ended the turn while the caller was still talking: the caller has to repeat) and *median
   EOU delay*. Report both per language bucket (English, Hindi, Hinglish, regional accents). An
   aggregate hides that one group is being cut off.

What we would not do is lower the VAD threshold to "catch" quiet speakers. That trades cut-offs
for background noise being treated as speech, which breaks barge-in (§4).

## 2. On-device vs server-side VAD

| | On-device (in the caller's app / handset / edge gateway) | Server-side (what we run) |
|---|---|---|
| Latency to detect speech | Lowest: no network hop; can stop local playback in ~one frame | One network hop each way. Barge-in reaction includes uplink jitter, and the cancel must travel back to stop playback that is already buffered on the device |
| Bandwidth / cost | Can send audio only while speaking (DTX), which cuts uplink and server compute | Must receive the full stream to decide what is speech |
| Echo | Has the playback reference locally, so acoustic echo cancellation (AEC) is natural here, which also prevents self-interruption | Sees the caller's mic *after* their device's AEC, which may or may not exist (PSTN, speakerphones) |
| Model control and consistency | Fragmented: different devices, versions, OS VADs; hard to update | One model, one threshold set, can be updated and A/B tested centrally |
| Observability | Weak: decisions happen where you can't log them | Every decision logged with the audio that caused it (our `events.jsonl`) |
| Works on PSTN / telephony | No: a phone call gives you no client code | Yes |

Our choice for this system: **server-side VAD as the source of truth for turn-taking**, because a
support line must work on plain phone calls, where there is no client to run code on, and because
turn decisions must be logged and tuned centrally. Where we control the client (app or web
call), add a **cheap on-device VAD as a hint** that stops local playback immediately (fast duck),
while the server makes the confirm/cancel decision. That is the same two-stage duck → confirm
design we use server-side, split across the network, so the caller gets instant feedback without
the client being trusted with the final decision.

## 3. Streaming vs batch ASR

We use **batch** ASR (Groq Whisper, local faster-whisper int8 as fallback) on each endpointed
utterance. Batch is the right default when:

- utterances are short (support turns are mostly 1–5 s), so the "wait until the end" cost is
  small compared with LLM time;
- accuracy on the whole utterance matters more than early partials: order ids, error codes and
  amounts are exactly the tokens streaming models get wrong early and revise later;
- the provider only offers batch (Groq's Whisper endpoint is batch), and a batch fallback is
  trivially swappable: the same audio goes to any provider, which is what made our STT fallback
  chain simple.

The cost is that STT time sits on the critical path *after* EOU (it's the second-largest stage in
§5 after the LLM). Streaming ASR is the better pick when:

- you want **semantic end-of-turn** (§1.3), which needs partial transcripts while the caller is
  still talking;
- you want to **start work before the caller finishes**: route the intent, fetch the order, or
  prefetch retrieval on a stable partial, so the LLM starts immediately at EOU;
- utterances are long (complaints, explanations), where batch latency grows with length;
- barge-in needs to know *what* was said, not just that speech happened (e.g. ignore "uh-huh"
  but stop on "wait"). With partials, a backchannel can be recognised as one within ~300 ms.

Production design: streaming ASR for partials (turn detection, early intent, backchannel
classification), with a **final batch pass** on the complete utterance for the transcript that
drives tools. That is two STT costs per turn, but the tool-driving text is the accurate one.

## 4. Barge-in and turn ownership (what the code guarantees)

- **Two-stage barge-in.** While the assistant speaks, frames keep flowing through VAD. A short
  run of speech **ducks** playback (lowers gain); only sustained speech **confirms** the barge-in,
  stops the sink and cancels the TTS stream. Speech that stops before confirmation un-ducks and
  playback continues. Noise bursts are rejected twice: by the neural VAD (low speech probability)
  and by the duration gate.
- **One owner of playback, monotonically increasing turn ids.** Every transcript, reply and TTS
  chunk is tagged with its turn id; anything for a stale turn is dropped and logged. A barge-in
  continues the *same* captured speech as the next utterance (pre-roll included), so the
  interrupting words are transcribed once, not twice.
- **Cancel and merge.** If the caller speaks again while the previous turn is still thinking,
  the in-flight work is cancelled and the two transcripts are merged, so "where is my order" +
  "wait, wrong order, PN-10005" becomes one turn with full context, not two answers.
- **Every external await has a timeout.** STT, TTS first chunk, TTS inter-chunk and the responder
  all have one, with canned audio ("sorry, I didn't catch that", "one moment", "trouble on my
  side") as the floor. A provider can fail; a turn cannot hang.

## 5. Measured latency

Instrumented, not estimated. The clock starts at **the end of the caller's last voiced 32 ms
frame** (not at the end-of-utterance decision), so the hangover we wait for is counted. Run:
`demo/runs/10_latency_run/20261020-100303-groq/latency.json`, 10 turns, **real recorded caller
clips**, Groq Whisper `whisper-large-v3`, Groq LLMs (router `gpt-oss-20b`, guards `gpt-oss-120b`,
agent `qwen3.8-27b`), in-house streaming TTS, Silero VAD.

| | p50 | p95 |
|---|---|---|
| End of speech → first audio (the "one moment" filler) | **1381 ms** | 1387 ms |
| End of speech → first **answer** audio | **5316 ms** | 6546 ms |
| Stage: hangover (silence needed to declare end of utterance) | 576 | 576 |
| Stage: STT (Groq whisper-large-v3) | 669 | 761 |
| Stage: LLM (text pipeline: guard, router, agent, output guard) | 2755 | 4066 |
| Stage: TTS first chunk (whole reply in one request) | 1273 | 1471 |
| Stage: playout | 0.3 | 0.4 |

Why the number is what it is:

- **The LLM is ~52% of answer latency** (2.8 s): a turn is 4–5 sequential calls (input guard,
  router, agent with a tool call and a final answer, output rubric) at 0.3–0.7 s each on Groq.
  The levers are fewer sequential calls (run the input guard and router in parallel; skip the
  output rubric when the structural check already passed on confirmed fields) rather than a
  faster model. For comparison, the same run on our in-house server (~10 tokens/s) was 10.6 s,
  with the LLM at 7 s.
- **STT 0.67 s.** We use full `whisper-large-v3`, not turbo, at the same Groq latency: on our real
  recordings turbo dropped a digit from every spoken order id ("PN-10002" → "PN-1002", 3/3 clips),
  and large-v3 with the vocabulary prompt got 3/3 right.
- **Hangover 576 ms** is fixed (§1). A sweep found 320 ms split a real sentence; the cut-off risk
  outweighs ~250 ms.
- **TTS first chunk ~1.3 s.** Sending the whole reply as one request (one TTS session keeps the
  voice continuous) costs ~350 ms more than per-sentence requests. We chose voice continuity.
- **The filler is why the caller heard something at 1.4 s** in this run, where it fired 0.8 s
  after end of utterance. We report both numbers because they answer different questions: "did
  the line acknowledge me?" (1.4 s) and "when did I get my answer?" (5.3 s). We don't present
  the first as the second.
- **The filler now waits 3 s** (changed after this run). In live calls at 0.8 s it played on
  almost every turn, including a bare "hello", and sounded robotic. At 3 s it plays only when
  the caller has actually been waiting; first audio becomes ~3.6 s on slow turns and the answer
  itself on fast ones. The 1.4 s figure above is therefore the 0.8 s configuration.
- **Rate limits are part of latency on the free tier.** Groq caps each model at 8k tokens/min and
  a turn is ~3k tokens, which is why roles are spread across three models. Past that budget,
  calls fail fast into the deterministic fallbacks rather than wait.

Silero VAD costs 0.73 ms mean per 32 ms frame (p95 1.1 ms, 11,296 frames); maximum frame lag in
the control loop was 7.9 ms.

Barge-in reaction on the real recordings: caller starts "wait, wrong order" → playback cut
760–840 ms later (scenarios 03, 06, 12: duck at 160 ms of speech, confirm at 560 ms). A real
clap and real keyboard typing caused no duck at all; a real "Uh." and "Uh-huh." ducked and
resumed.


## 6. Known failure cases (not fixed)

Found in the scenario runs and tuning sweeps; full list in [`red_team.md`](red_team.md).

1. **Backchannel rejection rests on a thin, voice-specific margin.** "Uh-huh" over background hum
   confirmed a barge-in 11/11 times at a 480 ms confirm threshold and 0/11 at 560 ms; we ship
   560. That margin was tuned on one synthetic voice and has since held on one real speaker's
   "Uh-huh." (one recording); a drawn-out "mmm-hmm" will still cut the assistant off. The
   robust fix is a transcript-based backchannel classifier with resume-after-cut (streaming
   ASR, §3). Not built.
2. **Speech at the very end of an answer, or over the filler, always becomes a turn.** A cough over
   "one moment" cancels and restarts the 2–4 s LLM call. Deliberate: the opposite policy dropped
   real short utterances ("PN-10005" said over the filler) in the stage sweep.
3. **An interrupted reply stays in session history in full**, although the caller heard only part
   of it. The pipeline saves the reply before playback; fixing it needs a deferred-save API.
4. **No echo cancellation in local mic mode.** The CLI mic mode (`support-voice`) needs
   headphones, or the assistant's own voice is heard as a barge-in.
5. **Fixed hangover.** Hesitant speech (reading out digits) can split into two segments. They are
   merged, so no turn is lost, but the merge costs an STT/LLM restart.
6. **One speaker, one room.** The scenario runs use real recordings, but of one speaker on a
   laptop mic. Thresholds (VAD, duck/confirm, hangover) have not been checked across accents,
   phone-line audio or noisy rooms (§1.4).
