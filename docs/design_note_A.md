# Design note A: the agentic support system

Answers the three required questions, plus the guardrail vs tool-agent reconciliation the brief
asks to have documented. Longer treatments: [`scaling.md`](scaling.md) (capacity math, what
breaks first) and [`guardrail_trace.md`](guardrail_trace.md) (structural no-bypass trace).

## 1. Scaling to 10k concurrent sessions

- **The app tier is not the bottleneck; the LLM is.** Workers are stateless async processes.
  Sessions, the classifier cache and handoff tickets live in Redis (`REDIS_URL`), so any replica
  serves any turn and replicas scale horizontally behind a load balancer.
- **10k concurrent sessions is not 10k concurrent LLM calls.** People think and type: at one turn
  per ~20 s per session that is ~500 turns/s, and each text turn makes 3–4 LLM calls (input guard,
  router, agent, output rubric). The capacity plan in `scaling.md` sizes the model fleet in tokens
  per second, not sessions.
- **Spend fewer and cheaper tokens per turn:**
  - the router and guard run on a small model (`LLM_MODEL_ROUTER`, per-role models);
  - the classifier cache skips the router call for repeated messages;
  - deterministic paths skip LLM calls entirely (escalation, templated tool answers);
  - we stopped generating unused tokens. Measured on our server, decode is ~100 ms/token, and a
    free-text router rationale was ~3 s of every turn until we removed it.
- **Backpressure instead of collapse.** Per-role concurrency limits in the LLM client, a per-turn
  time budget with per-stage slices, a circuit breaker, and a deterministic fallback at every LLM
  stage. Overload produces worse answers, not hung requests. Not yet built: admission control
  (503 + Retry-After) at the API.
- **Know what breaks first** from the OTel metrics we already emit: per-role LLM latency and
  errors, queue wait on the concurrency limit, cache hit rate, degraded-route and heuristic-only
  guard counts.

## 2. Adding a 4th agent (e.g. `billing_dispute`) without breaking routing

1. Add the intent value to `Intent` (contracts), write a `Handler` subclass with a `description`,
   and register it in the `HandlerRegistry` in `harness/factory.py`.
2. The router prompt and its JSON-schema enum are **generated from the registry**, so the router
   code doesn't change. The cache key includes a hash of the rendered router prompt, so cached
   decisions made without the new intent are invalidated immediately (a test covers registering
   an extra handler).
3. Add `billing_dispute` rows **and confusable negatives** to the routing eval: refund vs billing
   dispute, "I was charged twice" vs "return my order". Run the eval. The gate is that the
   existing intents' per-intent recall doesn't drop more than 2 points, and that the new intent's
   confusion pairs are reviewed.
4. Ship behind a shadow/canary split by conversation (see Part C §C2), because routing changes
   affect every message, not just billing ones.
5. If the new agent can state amounts (a billing dispute will), its tool must mark which fields
   are confirmed, and the output guard enforces the same confirmed-field rule. Nothing new is
   needed in the guard.

## 3. Eval set and metrics

| Set | Rows | Measures | Metric |
|---|---|---|---|
| `eval/data/routing_eval.jsonl` | 180 | Router (behind the input guard, as in production) | accuracy, per-intent precision/recall, confusions, cache hit rate |
| `eval/data/injection_eval.jsonl` | 120 | Input guard on generic attacks (deepset) | recall, false-positive rate |
| `eval/data/adversarial_domain.jsonl` | 42 | Input guard on PulseNest-specific attacks and benign-but-suspicious messages | recall, false-positive rate |
| `eval/data/adversarial_output.jsonl` | 15 | Output guard on drafts that must pass or be rewritten | correct pass/rewrite |
| `eval/data/retrieval_eval.jsonl` | 52 | Retrieval (44 answerable, 8 out-of-scope) | recall@1/@3, MRR, no-answer rate |

Why both guard sets: the generic set measures breadth; the domain set measures the attacks that
actually cost money here (fake refund approvals, fake policy quotes, the TH-01 hygiene rule,
medical-reading bait). Guard recall is reported **with and without the LLM**, because during an
LLM outage only the deterministic layer runs.

Runner: `uv run python eval/run_text_eval.py` → `eval/results/text_eval_<date>.json`.

### Measured (2026-10-20, Groq)

Source: `eval/results/text_eval_2026-10-20.json`. Router `openai/gpt-oss-20b`, input guard
`openai/gpt-oss-120b`, 2 rows in flight, paced to 10 rows/min to stay under Groq's free-tier
token-per-minute cap.

**Routing** (180 rows, behind the input guard as in production):

| Metric | Result |
|---|---|
| Accuracy (7 rows blocked by the input guard are excluded) | **85.0%** (unambiguous rows 86.0%) |
| Per-intent recall: escalate / order_status / product_question / return_refund | 94% / 100% / 83% / **58%** |
| Per-intent precision, same order | 77% / 96% / 92% / 76% |
| Top confusions | return_refund → escalate_to_human (13), product_question → return_refund (5) |
| Routes that fell back to the rule-based router | 3 / 180 |
| Classifier cache hit rate, repeat pass over the same 180 messages | **94.4%** |

**Input guard: only the deterministic layer was measured.** The guard model's free-tier daily
budget (200k tokens/day for `gpt-oss-120b`) ran out during the routing passes, which also run the
guard on every row, so the guard sets ran heuristics-only on 118/120 and 41/42 rows. These are
therefore the numbers for **an LLM outage**, not for the full guard:

| Set | Recall | False-positive rate |
|---|---|---|
| PulseNest adversarial (42: 24 attacks, 18 benign-but-suspicious) | **83.3%** (20/24) | **5.6%** (1/18) |
| Generic deepset injections (120) | **5.0%** (3/60) | 0% |

The full LLM-backed guard was not measured on Groq; re-running `eval/run_text_eval.py --only
guards` with budget left (or a second key in `LLM_API_KEYS_EXTRA`) gives it. An earlier in-house
run (`text_eval_2026-10-19.json`, overloaded: 63/180 routes degraded) had 80% routing accuracy and
the same picture for the guard.

What this says:
- **`return_refund` is the weak intent** (58% recall): most misses are Bitext refund and
  cancellation rows routed to escalation. Some are defensible ("cancel my order" has no tool here,
  so a human is the right destination), which means part of this is a labelling question in our
  own eval set, not only a router error.
- **The deterministic layer is a domain-specific backstop, not a general injection detector**:
  83% on PulseNest attacks, 5% on generic deepset attacks (role-play, off-topic tasks, German
  prompts). That is acceptable only because the LLM classifier normally runs in front of it, and
  this run shows that a free-tier daily cap is enough to take it away.
- **Misses on the domain set (deterministic layer):** a claimed supervisor role, "insider" hearsay
  about marking orders inspection-passed, an acrostic exfiltration, and a Hinglish instruction
  override. **False positive:** "I'm the developer of a fitness app…".
- **Capacity:** one turn is ~3k tokens across roles, so a single free-tier model allows ~2.5 turns
  a minute; the guard alone uses ~1.2k per turn, so its 200k/day covers ~160 turns. That, not code, is what limits this deployment; it is why
  roles are spread across models and why paid tiers or several keys are a prerequisite for real
  traffic.


## 4. Choices the brief asks us to justify

**Embeddings: local `sentence-transformers/all-MiniLM-L6-v2`, cosine threshold 0.35.** Groq
serves no embeddings, so retrieval runs locally on CPU (no extra provider, no per-query cost, no
outage coupling to the LLM). We measured three options on our own 52-query set (44 answerable, 8
out-of-scope; `eval/results/retrieval_2026-10-19.md`):

| Method | recall@1 | recall@3 | MRR | p50 latency |
|---|---|---|---|---|
| BM25 (keyword baseline) | 0.773 | 0.886 | 0.852 | ~0 ms |
| BAAI/bge-small-en-v1.5 | 0.818 | 0.977 | 0.896 | 10.6 ms |
| **all-MiniLM-L6-v2** | **0.886** | **1.000** | **0.932** | 7.0 ms |

MiniLM wins on accuracy, but the deciding reason was **score separation**: on bge, 62% of
out-of-scope queries scored ≥ 0.50, so no threshold cleanly separated "answerable" from "say I
don't know". MiniLM's scores separate at 0.35. Caveat: threshold calibrated and evaluated on the
same 52 queries (red-team #12). With 23 chunks the index is a numpy matrix; a vector DB would be
needed only at a much larger KB.

**Tool agent: native function calling, not a ReAct text loop.** Our tools are two read-only lookups
with typed arguments (`order_id`, `opened`). Function calling gives structured, pydantic-validated
arguments with no free-text parsing, and one call usually settles the turn, where a ReAct loop
spends extra LLM round-trips (we measured ~100 ms per decoded token on our server) and adds a
parsing failure mode. Safety doesn't depend on the model's tool choice: each intent has a tool
allowlist, the order id must come from the customer's own text, and if the model emits no tool
call (some vLLM servers have tool calling off) a deterministic "obvious tool" call runs instead.
ReAct would earn its cost with multi-step plans across many tools, which this domain doesn't have.

## 5. Guardrail vs tool agent: how they are reconciled

The tool agent can settle return eligibility in one turn; the output guard forbids promising a
refund amount or date the system hasn't confirmed. Neither silently overrides the other:

- Tools mark which fields the system of record **confirmed** (`ToolResult.confirmed_fields`).
  `eligible`, `return_window_ends` and `restocking_fee_pct` are confirmed;
  `refund_estimate_inr` never is, because the final amount depends on inspection.
- The output guard checks every amount and date in a reply against confirmed fields. A confirmed
  verdict passes through. An unconfirmed estimate is replaced by a fixed statement: "the final
  refund amount is confirmed after the item passes inspection, 5–7 business days".
- The rewrite is **visible**: `rewritten=true` on the reply, a guard note naming what was kept
  and what was replaced, and an OTel span event. If an over-eager rubric removes a confirmed fact,
  the guard re-states it from the confirmed field. This is visible in the demo:
  `Can I return PN-10004? I opened it.`
