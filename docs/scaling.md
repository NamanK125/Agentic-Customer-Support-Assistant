# Scaling to 10k concurrent sessions

The short answer: the app tier is easy, Redis is easy, and the LLM fleet is the whole problem.
Everything below is an estimate. The in-house endpoint was unreachable while this was written, so
no throughput figure here is measured. Each assumption is stated so it can be swapped for a load-test
number (`vllm benchmark_serving` against the real prompts) without redoing the reasoning.

## 1. What "10k concurrent sessions" means in load

A session is an open conversation, not an in-flight request. People read, think and type, so assume
**one turn per session every ~30 s** at peak:

- turn rate ≈ 10,000 / 30 ≈ **330 turns/s**
- in-flight turns (Little's law) ≈ 330 turns/s × ~3 s per turn ≈ **1,000 concurrent requests**

If sessions are voice calls, turns come faster (one every ~8–10 s), so turn rate roughly triples.
That changes the GPU count below by the same factor and nothing else.

## 2. The real bottleneck: LLM tokens

LLM calls per turn on a cache miss (prompt / output tokens, estimated from the prompts in this repo):

| Call | Model role | Prompt | Output |
|---|---|---|---|
| Input guard | guard | ~700 | ~30 |
| Router | router | ~500 | ~40 |
| Handler (RAG ~1,300/150; tool agent 2 rounds ~1,700/150) | agent | ~1,500 | ~150 |
| Output guard | guard | ~1,000 | ~40 |
| **Total** | | **~3,700** | **~260** |

At 330 turns/s that is **~1.2M prompt tok/s and ~86k output tok/s**.

**Capacity assumption (unverified):** one L40S/A100-class GPU serving `Qwen/Qwen2.5-7B-Instruct-AWQ`
on vLLM with continuous batching gives roughly 25k prompt tok/s of prefill and 2.5k output tok/s of
decode, while per-call p95 stays under ~2 s.

Naive sizing (everything on 7B, no reuse):

- decode: 86k / 2.5k ≈ 35 GPUs
- prefill: 1.2M / 25k ≈ 49 GPUs → **~50 GPUs**, prefill-bound

The app tier (section 4) needs about 10–20 vCPUs. The LLM fleet is one to two orders of magnitude
more expensive, so every optimisation that matters is about tokens:

1. **Prefix caching** (`--enable-prefix-caching`). System prompts are static and make up about half
   of all prompt tokens, so this cuts prefill roughly 2× at no quality cost. It is the cheapest win.
2. **Size the model to the role.** The router and input guard are short-output classifiers, so they
   can run on `Qwen/Qwen2.5-1.5B-Instruct-AWQ`. The output guard can run on
   `Qwen/Qwen2.5-3B-Instruct-AWQ`. Only answer generation needs the 7B AWQ build. A 1.5B model
   costs about a quarter of a 7B per token, and the two classifier calls are ~1,200 of the ~3,700
   prompt tokens. `Settings.model_for(role)` already routes per role. The cost: each small model
   needs its own eval pass, because routing accuracy and guard recall are what this system is graded
   on.
3. **Skip LLM calls that rules can decide.** For example, the output guard does not need an LLM
   judge for a draft with no amounts, dates or policy verbs. It trades some rubric coverage for
   latency, so it is a guardrail decision, but it is the biggest remaining lever after (1) and (2).

With (1) and (2) the estimate drops to roughly **12–20 GPUs**. That is the number to validate first.

### Classifier cache: a latency win, not a capacity win

The cache removes only the router call, which is ~13% of prompt tokens. A realistic hit rate for
support traffic is 20–40%: order ids are masked in the key and normalisation ignores case and
punctuation, but context-dependent replies ("yes") only hit when the previous assistant turn
matches too. That saves about 3–5% of fleet tokens. What it really buys:

- ~200–400 ms off the critical path on a hit;
- an outage cushion: hits keep routing on the LLM path even while the LLM is degrading.

Report it honestly as "hit rate X%, tokens saved Y%", not as a scaling strategy. The Redis-backed
cache is shared, so the hit rate is fleet-wide rather than divided by the replica count.

## 3. Bounded concurrency and backpressure to the LLM

vLLM does not reject excess load; it queues it. Without our own limits, overload looks like this:
latency grows, every turn hits the 30 s budget, `LLMClient` retries timeouts (2×), and the retries
add more load. That is a retry storm. What is needed:

- **Per-process, per-role semaphore** around `LLMClient.chat`, sized to fleet batch capacity ÷
  replica count. Time spent waiting for a permit counts against the turn budget, so a starved turn
  degrades instead of queueing forever. *Implemented in `llm/client.py` (`LLM_MAX_CONCURRENCY`, default 32 per role per process; wait time is on the span as `app.llm.queue_wait_ms`).*
- **Admission control at the API**: when in-flight turns per pod exceed a limit, return
  `503 + Retry-After` (or a fast canned "busy, a human will follow up" reply) instead of accepting
  work that will time out. *Not implemented yet.*
- **Retry budget**: retries capped at ~10% of calls fleet-wide, not "2 per call".
- **429 handling** (Groq or any rate-limited provider): key rotation and jittered exponential
  backoff already exist in `LLMClient`. The semaphore keeps us from causing the 429s.

What is implemented: a per-turn budget (`TURN_TIMEOUT_S` setting, default 30 s), a router cap (⅓ of the
budget, plus an 8 s LLM timeout inside the router), a reserved slice for the output guard, and a
deterministic degraded path at every LLM stage. Rule-based routing, templated tool answers from
confirmed fields, and escalation all mean overload produces worse answers rather than hung requests.

## 4. App tier: stateless async workers

- One async uvicorn worker per container, and scale by replicas. A turn spends >95% of its time
  awaiting I/O, so one process holds hundreds of in-flight turns.
- CPU per turn is pydantic/JSON (a few ms), plus a query embedding for RAG turns (bge-small on CPU,
  ~10–20 ms). At 330 turns/s that is ~3–5 CPU-seconds per second, so **10–20 one-vCPU pods**
  including headroom and failure domains. Pod memory (torch + embedding model, ~1 GB) sets the pod
  size, not CPU. The embedding call must run off the event loop (thread pool or a separate
  embedding service such as TEI), or one slow encode stalls every turn in that process.
- All state is behind ports (`SessionStore`, `ClassifierCache`, `HandoffStore`), so any replica can
  serve any turn and pods can be killed freely. With `REDIS_URL` unset, the same code runs in-memory
  for a single box.
- One `AsyncOpenAI` client per process (httpx keep-alive pool), built once in the composition root.
  Redis uses a pool of up to 200 connections per pod, and nginx keeps upstream connections alive.
- Autoscale on **in-flight turns per pod**, not CPU. CPU stays low while turns wait on the LLM, so a
  CPU-based autoscaler never fires. The GPU tier scales separately behind a
  least-outstanding-requests balancer.

## 5. Redis

Per turn: LRANGE + RPUSH/LTRIM/EXPIRE for the session, GET (+SET on miss) + INCR for the cache. At
~6 ops/turn that is ~2k ops/s, where one Redis node handles ~100k. Memory: 10k sessions × 10 turns
× ~400 B ≈ 40 MB, plus cache entries. Run one primary with a replica (managed or Sentinel). Failure
behaviour is coded, not hoped for:

- cache errors count as a miss;
- session errors mean the turn runs without history, which is still answered;
- a handoff-store error fails the escalation turn to the safe fallback reply. That is a known gap:
  the one path that should never fail depends on Redis.

## 6. What breaks first, and how you would know

In expected order:

1. **LLM queueing.**
   - Signals: `llm.request.duration` p95 by role rising (client histogram); vLLM's
     `num_requests_waiting` and `gpu_cache_usage_perc`; then
     `support.stage.duration{stage="router",outcome="timeout"}`, `app.router.degraded=true` spans
     and `support.turns.failed{stage}`.
   - Alerts: turn p95 above SLO (e.g. 6 s text) for 5 min; failed-turn rate > 1%; degraded-routing
     share > 5%; KV-cache usage > 90%.
2. **Event-loop stalls on app pods** (embedding or other CPU work inline).
   - Signals: `support.stage.duration{stage="handler"}` rising for RAG while `llm.request.duration`
     stays flat. Add an event-loop-lag gauge.
3. **Redis** latency or connection errors.
   - Signals: `session.load_failed` span events; `router.cache.hits` dropping to zero while misses
     continue.
4. **Humans.** At 5% escalation, 330 turns/s is ~16 new tickets per second. The handoff queue length
   (`sa:handoff:queue`) is a business-capacity alert, not an infrastructure one.

`support.turns`, `support.turns.blocked` and `support.turns.rewritten` by intent are the baseline:
a sudden shift in blocked or rewritten rate after a deploy is a guardrail regression, not traffic.

## 7. Adding a 4th agent (e.g. `billing_dispute`) without breaking routing

Mechanically, it is "write a Handler, register it, add eval rows":

- The router's prompt and JSON-schema `intent` enum are generated from `HandlerRegistry`, so the
  router has no per-agent code. `test_new_handler_is_routable_without_router_changes` proves this.
- The registry rejects duplicate intents, so a new agent cannot silently take over an existing one.
- The cache key includes a hash of the rendered router prompt. Registering a handler therefore
  invalidates old classifications instead of serving pre-change routes until TTL.
- Limitation: `Intent` is a closed `StrEnum` in `contracts.py`, so a genuinely new intent value
  needs a one-line contract edit. That is acceptable, and type safety is worth it, but it means
  contracts are not "untouched".

What actually breaks routing is semantic overlap, not code. "I was charged twice, I want my money
back" sits between `billing_dispute` and `return_refund`. The gate:

- **Eval regression gate in CI:** the routing eval set runs against the new registry. The build
  fails if any existing intent's F1 drops more than ~2 points, if `escalate_to_human` recall drops
  at all, or if guardrail recall changes.
- **New-intent rows:** at least 30 for the new intent, including near-miss rows labelled to its
  neighbours (`return_refund`, `escalate_to_human`), so the confusion matrix shows what it steals.
- **Shadow, then canary:** run the new router alongside the old one on live traffic, log
  disagreements, review a sample, then canary 5% with `support.turns{intent}` mix and escalation
  rate compared against control.
- **Guardrails:** the new agent's claims (amounts, dates) must come back as `ToolResult` confirmed
  fields, so the existing output guard covers it with no new guard code.
