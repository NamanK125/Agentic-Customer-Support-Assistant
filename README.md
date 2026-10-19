# Agentic Customer Support Assistant

Customer support assistant for **PulseNest**, a fictional brand of home health-monitoring devices
(blood pressure monitor, pulse oximeter, thermometer, smart scale, companion app). The domain,
policies and forbidden claims are defined in [`docs/domain.md`](docs/domain.md).

| Brief | Where |
|---|---|
| Part A: agentic system (router, RAG, tools, guardrails, cache) | `src/support_assistant/{harness,rag,tools,agents,guardrails}`, [design note A](docs/design_note_A.md), [guardrail trace](docs/guardrail_trace.md) |
| Part B: voice turn-taking (barge-in, resilience, latency) | `src/support_assistant/voice/`, [design note B](docs/design_note_B.md), scenario runs in `demo/runs/` |
| Part C: integration and ops | [docs/part_C_answers.md](docs/part_C_answers.md) |
| Decisions log | [docs/decisions_log.md](docs/decisions_log.md) |
| Red-team (failures we found and didn't fix) | [docs/red_team.md](docs/red_team.md) |
| Presentation | [slides/PulseNest_Support_Assistant.pptx](slides/PulseNest_Support_Assistant.pptx) |

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

## Voice

```
caller audio ─► Silero VAD + endpointer ─► STT (Groq Whisper → local faster-whisper int8)
   ─► the same text pipeline (channel="voice")
   ─► TTS (streaming in-house TTS → Edge TTS → canned audio) ─► caller
```

- **Barge-in, two-stage:** speech during playback ducks it (160 ms), sustained speech (560 ms)
  cuts it and becomes the next turn; noise and "uh-huh" un-duck and playback continues.
- **No double-handled or dropped turns:** one control loop owns all call state, every result
  carries its turn id, stale results are dropped, and an unheard turn is merged into the next.
- **Resilience:** per-attempt timeouts, circuit breakers and fallback chains for STT and TTS; a
  filler ("one moment") and canned apologies so a turn never goes silent or hangs.
- **Latency:** instrumented from the end of the caller's last voiced frame, per stage.
- **One TTS voice** (`TTS_SPEAKER`) for every request of every call.

```bash
uv run python scripts/run_scenario.py demo/scenarios/03_real_interruption.json   # scripted, real providers
uv run support-voice                                                           # live, mic + speakers (headphones)
uv run python scripts/record_clips.py                                          # record real caller clips
```

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env          # set LLM_API_KEY (Groq by default)
uv run python scripts/build_index.py
```

Any OpenAI-compatible endpoint works. `.env.example` shows the settings for a self-hosted vLLM
server.

## Run

```bash
uv run support-assistant                      # interactive chat
uv run support-assistant --once "Where is my order PN-10002?"
uv run python scripts/smoke.py                # one turn per path (product, orders, returns, injection, medical)
uv run uvicorn support_assistant.api:create_app --factory --port 8000   # POST /v1/chat
```

## Tests and checks

```bash
uv run pytest            # unit tests, all offline (FakeLLM)
uv run ruff check src tests scripts eval
uv run lint-imports      # the CheckedMessage seal contract
```

## Evaluation

`uv run python eval/run_text_eval.py` runs routing accuracy, guardrail recall/FPR and the
classifier-cache hit rate against the configured LLM (`eval/results/text_eval_<date>.json`).

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

- [`docs/decisions_log.md`](docs/decisions_log.md): design decisions in the order they were made, including
  abandoned approaches and open doubts
- [`docs/guardrail_trace.md`](docs/guardrail_trace.md): structural no-bypass trace, guardrail vs
  tool-agent reconciliation, outage policy, known failure modes
- [`docs/design_note_A.md`](docs/design_note_A.md): scaling, adding a 4th agent, eval set and metrics, guardrail vs tool reconciliation
- [`docs/design_note_B.md`](docs/design_note_B.md): end-of-utterance detection, on-device vs server VAD, streaming vs batch ASR, measured latency
- [`docs/part_C_answers.md`](docs/part_C_answers.md): voice-to-chat state, production eval strategy, Groq degradation and failover
- [`docs/red_team.md`](docs/red_team.md): failures found in our own system and why they're not fixed
- [`docs/scaling.md`](docs/scaling.md): capacity math for 10k concurrent sessions
- [`docs/domain.md`](docs/domain.md): the PulseNest domain the knowledge base and guards are built on
