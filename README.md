# Agentic Customer Support Assistant

Customer support assistant for **PulseNest**, a fictional brand of home health-monitoring devices
(blood pressure monitor, pulse oximeter, thermometer, smart scale, companion app). The domain,
policies and forbidden claims are defined in [`docs/domain.md`](docs/domain.md).

Status: the text side (Part A) is implemented. The voice pipeline (Part B) and the written
answers (Part C) are not in this version yet.

## How a turn flows

```
RawMessage ─► InputGuardrail ─► CheckedMessage ─► Router ─► Handler ─► DraftReply ─► OutputGuardrail ─► FinalReply
                    │                               │
                    └─► BlockedMessage (safe reply) └─ cache (hit rate reported)
```

| Intent | Handler | What it does |
|---|---|---|
| `product_question` | `RagAgent` | Dense retrieval over `kb/`, answers only from cited passages, no answer below a similarity threshold |
| `order_status` | `ToolAgent` | `get_order_status(order_id)` via function calling |
| `return_refund` | `ToolAgent` | `check_return_eligibility(order_id, ...)`; refund estimates are never stated as confirmed |
| `escalate_to_human` | `EscalationHandler` | Files a handoff ticket; deterministic, no LLM |

- **No unchecked path to an agent:** `CheckedMessage` can only be created with a capability seal
  that only the guardrails package may import (enforced by `lint-imports`), and handlers reject
  anything else at runtime. See [`docs/guardrail_trace.md`](docs/guardrail_trace.md).
- **Degraded mode:** every LLM stage has a deterministic fallback (rule routing, templated tool
  answers, extractive RAG, heuristic guards), plus a circuit breaker, per-role concurrency limits
  and a per-turn time budget. A turn never raises and never returns an unguarded draft.
- **Observability:** OpenTelemetry traces and metrics for every stage, exported over OTLP.
- **Scale-out:** stateless workers; sessions, classifier cache and handoff tickets in Redis when
  `REDIS_URL` is set. See [`docs/scaling.md`](docs/scaling.md).

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env          # set LLM_API_KEY (Groq by default)
uv run python scripts/build_index.py
```

Any OpenAI-compatible endpoint works. `.env.example` shows the settings for a self-hosted vLLM
server. Run `uv run python scripts/check_llm.py` to check what the configured endpoint supports
(structured output, tool calling).

## Run

```bash
uv run support-assistant                      # interactive chat
uv run support-assistant --once "Where is my order PN-10002?"
uv run python scripts/smoke.py                # one turn per path (product, orders, returns, injection, medical)
uv run uvicorn support_assistant.api:create_app --factory --port 8080   # POST /v1/chat
docker compose -f deploy/docker-compose.yml up   # 3 app replicas + nginx + Redis + Jaeger
```

## Tests and checks

```bash
uv run pytest            # unit tests, all offline (FakeLLM)
uv run ruff check src tests scripts eval
uv run lint-imports      # the CheckedMessage seal contract
```

## Evaluation data

| File | Rows | Purpose |
|---|---|---|
| `eval/data/routing_eval.jsonl` | 180 | Routing accuracy (Bitext mapped to our intents + hand-written PulseNest questions) |
| `eval/data/injection_eval.jsonl` | 120 | Generic prompt-injection recall / false positives (deepset) |
| `eval/data/adversarial_domain.jsonl` | 42 | PulseNest-specific attacks and benign-but-suspicious messages |
| `eval/data/adversarial_output.jsonl` | 15 | Output-guard drafts that must pass or be rewritten |
| `eval/data/retrieval_eval.jsonl` | 52 | Retrieval recall/MRR and threshold calibration |

Sources, licenses and caveats: [`eval/DATASETS.md`](eval/DATASETS.md). Embedding choice (MiniLM vs
bge-small vs BM25): [`eval/results/`](eval/results/). Rebuild the HF-derived sets with
`uv run python scripts/fetch_datasets.py`.

## Documents

- [`docs/decisions_log.md`](docs/decisions_log.md): timestamped design decisions, including
  abandoned approaches and open doubts
- [`docs/guardrail_trace.md`](docs/guardrail_trace.md): structural no-bypass trace, guardrail vs
  tool-agent reconciliation, outage policy, known failure modes
- [`docs/scaling.md`](docs/scaling.md): 10k concurrent sessions, adding a new agent
- [`docs/domain.md`](docs/domain.md): the PulseNest domain the knowledge base and guards are built on
