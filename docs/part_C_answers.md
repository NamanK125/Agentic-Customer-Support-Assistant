# Part C: integration and operations

Written answers. Where a claim rests on something we built or measured, the file is named; where
it is design only, it says so.

## C1. A call moves from voice to text chat mid-conversation. How do Part A and Part B share state?

**What is already shared.** The voice channel is not a separate bot. Every voice turn goes
through the same `SupportPipeline.handle(session_id, text, channel="voice", caller=...)` as text
chat: same input guard, router, agents, output guard, same session store. So the conversation
history (guarded user turns and guarded assistant replies), the classifier cache and the handoff
tickets already live in one place (Redis when `REDIS_URL` is set). The voice side adds the call's
event log, and the pipeline already accepts an optional `CallerProfile` (name, order id;
format-constrained) for details a channel collects outside the conversation.

**What a transfer needs**, as a design (not built):

1. **One conversation id, many channel legs.** Introduce a `conversation_id` that the session
   store, handoff tickets and the caller profile are keyed by; the voice call and the chat each
   get a channel-leg id that points at it. A transfer is then "attach a chat leg to an existing
   conversation", not copying state between systems. Today the session id *is* the conversation
   id, so the change is a key, not a rewrite.
2. **Transfer = a handoff ticket with a summary, not a raw transcript dump.** The escalation
   handler already writes a `HandoffTicket` (customer message, route rationale, order id, caller
   name). For a voice-to-chat transfer we would add: the guarded transcript, the confirmed tool
   facts (order status, eligibility verdict), what the caller was last told, and whether the last
   reply was interrupted. A human agent should not have to re-read audio-era turns to find the
   order number.
3. **What the caller actually heard, not what we generated.** Barge-in means the stored
   assistant reply can be longer than what was heard. The coordinator already records how much of
   a reply played (`played_ms` / `audio_ms_total` on `barge_in_confirmed`). The transfer summary
   should mark interrupted replies as "partially heard" so the chat agent doesn't say "as I told
   you" about something the caller never heard. Known gap today: the pipeline saves the full reply
   to history before playback, so a barge-in leaves an unheard reply in history (see red-team).
4. **Identity does not transfer for free.** The voice profile is caller-supplied, not verified.
   If the chat channel is authenticated (logged-in app), the transfer must re-check that the chat
   user owns the order id from the call before the chat agent reads out order details.
5. **Consistency under concurrency.** Voice and chat legs can briefly both be live. Writes go
   through the session store with per-conversation append ordering (Redis list `RPUSH`); a turn
   carries its leg id so the human's view interleaves correctly.

## C2. Production evaluation strategy for the combined system

Four layers, each with its own gate.

**1. Offline regression suite, on every change (CI gate).**
- Routing: `eval/data/routing_eval.jsonl` (180 rows, balanced over 4 intents; Bitext mapped to our
  intents plus hand-written PulseNest questions and 10 deliberately ambiguous rows). Metrics:
  accuracy, per-intent precision/recall, confusion pairs. Gate: no intent's recall drops more than
  2 points; ambiguous rows are reported, not gated.
- Guardrails: `injection_eval.jsonl` (120, generic deepset) and `adversarial_domain.jsonl` (42,
  PulseNest-specific). Metrics: recall on attacks and false-positive rate on benign. Gate: recall
  on the domain set must not drop at all (a missed refund-fraud pattern is the expensive failure);
  FPR may rise by at most 2 points. The guard is also scored **with the LLM disabled**, because
  the outage policy depends on the deterministic layer alone.
- Output guard: `adversarial_output.jsonl` (drafts that must pass or be rewritten). Gate: zero
  regressions; every unconfirmed amount/date must be rewritten.
- Retrieval: `retrieval_eval.jsonl` (recall@k, MRR, no-answer rate). Gate on recall@3.
- Voice: the 12 scenario files in `demo/scenarios/` run in virtual time with fake providers on
  every commit (fast, deterministic: barge-in decisions, no duplicate turns, no hang), and with
  real providers nightly.
- Measured numbers for the current build: `eval/results/text_eval_*.json`, `eval/results/retrieval_*.md`,
  `demo/runs/*/result.json`.

**2. Safety regressions are treated as blockers, not trade-offs.** A routing gain never buys a
guardrail loss. New attack patterns found in production (blocked-message reviews, red-team
sessions) are added to the domain adversarial set before any fix, so the fix is proven against it.

**3. Latency SLOs from the instrumentation we already emit.** Per-stage OTel histograms
(`llm.request.duration` per role, the pipeline's per-stage latency, voice `turn_end` events with a
stage breakdown). SLOs, per channel:
- voice: end-of-speech to first audio p95 < 1.5 s (filler counts; it is the "I heard you" signal),
  end-of-speech to first *answer* audio p95 < 4 s on a production-grade LLM;
- text: turn p95 < 3 s;
- STT/TTS: provider fallback rate < 1% of turns; zero turns over the hang budget.
Alert on burn rate (fast burn: 2% of the monthly error budget in 1 h), not on single spikes.

**4. A/B testing a routing change.**
- Shadow first: run the new router on live traffic in parallel, log both decisions, change
  nothing for users. Compare against the offline set and against human-labelled samples of the
  disagreements (only disagreements need labelling, which keeps cost down).
- Then a randomized split by **conversation** (not by turn, so one conversation never mixes
  routers), small slice first (5%).
- Primary metrics: misroute rate (proxied by "user rephrased within 2 turns", escalation after a
  wrong-agent answer, and human-labelled samples), containment (resolved without a human), CSAT
  where collected. Guardrail metrics are guardrails on the experiment: any increase in
  blocked-message rate or output-guard rewrites stops it.
- Note the cache: the classifier cache key includes a hash of the router prompt, so arms never
  share cached decisions (`harness/router.py::prompt_id`).

## C3. Groq degrades mid-launch (high latency, partial outage). Detection, fallback, and the cost in behaviour consistency

**Detection** (signals we already emit, thresholds are design):
- Per-role p95 of `llm.request.duration` and error rate from `llm.request.errors`, per provider.
- The client's circuit breaker state (`LLMClient`, 3 consecutive failures → open for 15 s) and
  429 counts from key rotation.
- Downstream symptoms: pipeline turns exceeding the stage budgets, router `degraded` decisions,
  input guard `heuristic_only` verdicts. Any of these rising is the system degrading itself.
- Trigger: p95 over 2x the SLO for 3 minutes, or error rate over 5% for 1 minute, or breaker open
  on more than 20% of pods.

**What happens automatically today, before any failover:**
- Every LLM stage has a deterministic fallback, so a Groq outage degrades answers rather than
  hanging them: rule-based routing, templated tool answers from confirmed fields only, extractive
  RAG answers (or "I don't know"), input guard on heuristics with the output guard switched to
  strict mode, escalation for anything else. The circuit breaker makes this fail fast instead of
  spending seconds on retries per call.
- In voice, the filler and canned phrases keep the line alive; the caller hears "one moment"
  within ~1.4 s regardless of the LLM.

**Would we fail over to another LLM provider? Yes, but per role and only to a pre-qualified model.**
The client is provider-agnostic (`LLM_BASE_URL` / `LLM_MODEL`, per-role overrides), so a second
OpenAI-compatible provider, or our own vLLM deployment of a quantized model, is a configuration
switch. The plan:
1. Router and guard roles fail over first: they are short, structured, JSON-validated
   classifications where model differences show up as measurable accuracy/recall changes.
2. The answering agent fails over last, and only to a model that has passed the same offline
   suite (routing accuracy, guardrail recall, output-guard pass rate, RAG grounding) within the
   regression gates above. A failover target is qualified **before** the incident, not during it.
3. Failback is gradual (canary) after the primary is healthy for a sustained window, so we don't
   flap.

**What failover costs in behaviour consistency, and how we contain it:**
- *Classification drift:* a different model routes borderline messages differently and calibrates
  confidence differently, so the 0.5 confidence threshold means something else. Mitigation:
  per-model thresholds from the offline run; cached router decisions don't cross models (the
  model id is in the cache key).
- *Guardrail drift:* the input classifier's recall and false-positive rate change. The
  deterministic layer and the structural output guard don't change, and they are the backbone:
  amounts and dates must still match confirmed tool fields whatever model wrote the reply. That is
  the main reason the output guard is deterministic.
- *Tool-calling differences:* function-calling formats and reliability differ (we hit this on our
  own vLLM: tool calling was off on the server). The tool agent already falls back to a
  deterministic tool call plus a templated answer, so a weaker tool-caller costs phrasing, not
  correctness.
- *Tone and length:* replies change style mid-conversation. For voice this also changes latency
  (longer replies → more TTS). Acceptable during an incident; it is visible in traces because the
  model id is on every span.
- *Session continuity:* history is plain text in the session store, so a conversation can
  continue on another model; prompts are the same; nothing provider-specific is stored.

The honest summary: failover keeps the system answering, and the deterministic guards keep it
safe, but answer quality and routing on edge cases will measurably shift. We decide in advance,
from the offline suite, how much shift we accept, and we label failover traffic so post-incident
review can see exactly which turns ran on which model.
