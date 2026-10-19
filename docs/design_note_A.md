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

### Measured (2026-10-19, in-house `Qwen3.8-27B-AWQ-INT4`)

Source: `eval/results/text_eval_2026-10-19.json`. Run with 12 rows in flight against an in-house
server that decodes ~10 tokens/s. **Read this caveat first:** under that load most LLM calls hit
their time budgets. 63 of 180 routing decisions fell back to the rule-based router, and the input
guard ran heuristics-only on 109/120 generic and 37/42 PulseNest rows. These numbers therefore
mostly measure the **degraded path under overload**, not the LLM-backed system. Re-run at lower
concurrency (`--concurrency 2`) or on Groq for the LLM-path numbers.

| Metric | Result |
|---|---|
| Routing accuracy (180 rows; 0 blocked by the input guard) | **80.0%** (unambiguous rows 80.6%) |
| Per-intent recall: escalate / order_status / product_question / return_refund | 96% / 91% / 83% / **47%** |
| Top confusions | return_refund → escalate_to_human (14), return_refund → product_question (7) |
| Input guard, PulseNest adversarial set (42) | recall **87.5%** (21/24), false-positive rate **5.6%** (1/18) |
| Input guard, generic deepset set (120) | recall **11.7%**, false-positive rate **0%** (heuristic-only on 109 rows) |
| Classifier cache hit rate, repeat pass over the same 180 messages | **50.6%** (degraded routes are deliberately not cached) |

What this says:
- The **deterministic guard is tuned for this domain's attacks and is not a general injection
  detector**: 88% on PulseNest attacks, 12% on generic deepset attacks (role-play, off-topic
  instructions, German-language prompts). That is acceptable only because the LLM classifier
  normally runs in front of it, and the run shows how often it doesn't under load.
- **`return_refund` is the weak intent.** Many Bitext refund/cancellation rows went to escalation,
  partly because the degraded keyword router ranks escalation words first, and partly because
  "cancel my order" has no matching tool here. This is a labelled-data and capacity problem
  before it is a prompt problem.
- **Misses on the domain set:** an acrostic exfiltration, a fake prior assistant turn ("I have
  approved your refund…"), and a Hinglish instruction override. **False positive:** "I'm the
  developer of a fitness app…".
- **Capacity:** 12 concurrent turns already exceed the guard (6 s) and router (8 s) budgets on
  this server. The system degrades as designed (no errors, no hangs), but quality drops. That
  is the clearest argument in this note for per-role smaller models and admission control.


## 4. Guardrail vs tool agent: how they are reconciled

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
