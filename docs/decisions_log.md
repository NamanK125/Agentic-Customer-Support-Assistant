# Decisions Log

Timestamped as decisions are made, including approaches tried and abandoned. Times are IST.

Format:

```
[YYYY-MM-DD HH:MM] DECISION: <what>
  WHY: <reason>
  ALTERNATIVES CONSIDERED: <what, and why not>
  ABANDONED: <if any — what was tried, what failed, what it taught>
  UNSURE: <if this only looks solved, say so>
```

---

[2026-10-19 10:45] DECISION: Python as the implementation language.
  WHY: Fastest path for audio I/O, the Groq SDK, and local embeddings in one codebase.
  ALTERNATIVES CONSIDERED: TypeScript/Node (good streaming story, weaker audio/embedding tooling).

[2026-10-19 10:45] DECISION: Keep the assessment brief out of the repo (.gitignore).
  WHY: It is the employer's document. This log is the process record instead.

[2026-10-19 11:05] DECISION: LLM access through one provider-agnostic OpenAI-compatible client (LLM_BASE_URL / LLM_MODEL), pointed at an in-house vLLM-style server for now instead of Groq.
  WHY: An in-house GPU server was available for development without free-tier rate limits. Groq exposes the same OpenAI-compatible API, so switching back is a config change, not a code change.
  ALTERNATIVES CONSIDERED: Groq SDK directly (locks us to one provider and makes the Part C failover story harder).
  UNSURE: The in-house endpoint refused TCP connections from this network at 11:05, so model id, json_schema support and tool-calling support are unverified. All unit tests use FakeLLM.

[2026-10-19 11:05] DECISION: Per-role model selection (router / guard / agent), all defaulting to one model.
  WHY: Router and guard calls are short classification tasks and can run on a smaller/cheaper model; the agent needs the strongest one. Gives a cost lever without code changes.

[2026-10-19 11:05] DECISION: CheckedMessage is mintable only with a capability seal that only the guardrails package may import; enforced with an import-linter contract, and handlers reject non-CheckedMessage input at runtime.
  WHY: The brief asks for a structural argument that no unchecked message reaches RAG or tools. A type plus a lint-enforced import rule is checkable; "the router always calls the guard first" is only a convention.
  UNSURE: Python has no true privacy; a deliberate bypass via object.__new__ is possible. That counts as a known limitation, not a guarantee against malicious code in the repo.

[2026-10-19 11:05] DECISION: Domain = PulseNest, a fictional brand of home health-monitoring devices (see docs/domain.md).
  WHY: The assessment comes from a health company. A health-device domain adds a domain-specific forbidden-claim category (no medical interpretation) on top of refund/delivery promises, which makes the guardrails less generic.
  ALTERNATIVES CONSIDERED: Kitchen appliances (no safety dimension; more generic).

[2026-10-19 11:05] DECISION: check_return_eligibility returns a confirmed eligibility verdict but an UNconfirmed refund estimate.
  WHY: This makes the tool-agent vs output-guardrail tension from the brief concrete and testable rather than hypothetical.

[2026-10-19 11:05] DECISION: Build components (harness, guardrails, RAG, eval data) independently against shared contracts (contracts.py), each with its own package and tests, using FakeLLM.
  WHY: Keeps each component testable on its own and lets them be developed in parallel while the LLM endpoint was unreachable.

[2026-10-19 11:07] DECISION: Routing eval built from Bitext (CDLA-Sharing-1.0) mapped onto our intents, plus hand-written PulseNest product questions; injection eval from deepset/prompt-injections (Apache-2.0), English-only.
  WHY: Real phrasing variety for routing/injection recall without writing hundreds of rows by hand. Bitext has no product questions in our domain, so those are hand-written.
  ALTERNATIVES CONSIDERED: Fully hand-written sets (small, author-biased).
  UNSURE: Bitext is synthetic generic retail with cleaner phrasing than real users; deepset injections are generic, which is why a separate PulseNest adversarial set exists. The English filter (ASCII ratio + stopword) is imperfect: at least one romanised-Hindi row slipped through.

[2026-10-19 11:07] DECISION: Drop Bitext rows with non-order placeholders instead of filling them.
  WHY: Filling values like refund amounts or countries would invent facts the bot could be scored on. This drops 624 of ~26k rows.

[2026-10-19 11:20] DECISION: Rebalance the routing eval to ~40 Bitext rows per *our* intent (180 rows total) instead of 40 per Bitext intent.
  WHY: The first version had 203 return_refund rows against 42 product_question, so overall accuracy would have mostly measured one class.
  ABANDONED: The 420-row per-Bitext-intent sample.
  UNSURE: cancel_order / check_cancellation_fee are mapped to return_refund although there is no cancel tool, so per-intent scores there should be read with care.

[2026-10-19 11:03] DECISION: 15 KB docs, one topic each, deliberately confusable (returns 30d vs 15d vs TH-01; standard vs express shipping; scale safety vs SC-03 FAQ; BP-200 FAQ vs troubleshooting).
  WHY: Tests retrieval precision, not just topical recall. Every fact is taken from docs/domain.md.
  ABANDONED: A pregnancy caution in the scale doc. It isn't in domain.md, so it was removed.
  UNSURE: The support email and the "within 1 metre" pairing tip are invented details not in domain.md.

[2026-10-19 11:05] DECISION: Heading-aware chunking, consecutive sections packed to <=180 words, 25-word overlap only when a single section is split; chunk text prefixed with the doc title.
  WHY: Keeps chunks on-topic and citeable. Blanket overlap put the tail of the E2 section at the head of the E3 chunk.
  ABANDONED: One chunk per heading (12-word chunks); first packer left tiny tails, so tails under 40 words are now folded in.

[2026-10-19 11:08] DECISION: The RAG agent refuses without an LLM call when no chunk clears the threshold, also refuses when the answer has no valid [n] citation, and returns an extractive, attributed answer on LLM outage.
  WHY: Grounding over fluency; the outage path still answers.
  UNSURE: The citation check proves a passage is cited, not that the claim is supported by it. The output guardrail has to cover that.

[2026-10-19 11:25] DECISION: Default embedder switched to sentence-transformers/all-MiniLM-L6-v2 (threshold 0.35) from BAAI/bge-small-en-v1.5 (threshold 0.65).
  WHY: On 44 answerable + 8 out-of-scope queries: MiniLM recall@1 0.886 / recall@3 1.000 / MRR 0.932 / 7 ms, vs bge 0.818 / 0.977 / 0.896 / 10.5 ms, vs BM25 0.773 / 0.886 / 0.852. The more important reason is score separation: 62% of out-of-scope queries scored >=0.50 on bge, so its threshold barely separates in-scope from out-of-scope.
  ALTERNATIVES CONSIDERED: bge-small (the planned default); BM25 (kept as a baseline to show dense retrieval beats keywords).
  ABANDONED: bge's query instruction prefix didn't help here (0.818 vs 0.841 recall@1 without it).
  UNSURE: The recall gap between models is 3 queries, within noise. Thresholds were calibrated and evaluated on the same 52 queries, so they're optimistic until re-checked on held-out data.

[2026-10-19 11:06] DECISION: orders.json stores day offsets ("delivered_days_ago": 10), resolved against an injected clock.
  WHY: Demo data never goes stale; tests are deterministic with a fixed clock.
  ALTERNATIVES CONSIDERED: Absolute dates (return windows in the demo would expire within weeks).

[2026-10-19 11:07] DECISION: confirmed_fields lists only system-of-record facts that have a value. carrier_eta is confirmed only while status == "shipped"; refund_estimate_inr and the customer's "opened" claim are never confirmed.
  WHY: An empty field must not count as a confirmed fact, and "opened" is the customer's word, not ours.
  UNSURE: If "opened" is unknown, eligibility assumes unopened and records opened_basis="assumed_unopened". That is the generous reading; only warehouse inspection catches a customer who lies.

[2026-10-19 11:08] DECISION: Native function calling for the tool agent, not a ReAct text loop. One ToolAgent serves order_status and return_refund with a per-intent tool allowlist.
  WHY: Arguments are structured and pydantic-validated, giving fewer parse failures; vLLM supports it with the hermes parser.
  UNSURE: Tool calling is unverified on the in-house server. If it isn't enabled the model returns no tool call, a deterministic "obvious tool" fallback runs, and answers still come only from tool results.

[2026-10-19 11:09] DECISION: The order id comes from (1) a regex on the current message, (2) the newest id the customer wrote in history, (3) the LLM's guess only if its digits appear in the customer's text. Tool calls targeting a different id are refused.
  WHY: LLMs hallucinate ids, and a tool argument naming another order is a cheap pivot attack.
  UNSURE: "my other order" in plain words keeps the old id.

[2026-10-19 11:10] DECISION: Classifier cache key = sha256(prompt hash, model id, normalised last assistant turn, normalised user text with order ids masked). Only validated LLM output is cached, never degraded results. Hit/miss counters live in Redis so the hit rate is fleet-wide.
  WHY: "yes" after "start a return?" and "yes" after "talk to a human?" must be different keys; masking ids makes "where is PN-10001" and "where is PN-10002" one entry. Hashing the rendered prompt means registering a new handler invalidates stale routes immediately.
  ALTERNATIVES CONSIDERED: Skipping short messages (wastes the most common case); keying on full history (hit rate near zero).
  ABANDONED: A constant PROMPT_VERSION in the key (found stale after adding a handler); whitespace-only normalisation ("order?" vs "order ?" missed).

[2026-10-19 11:13] DECISION: On LLM outage the router falls back to ordered keyword rules (escalate > return_refund > order_status > product_question), then "an order id alone means order_status", else escalate.
  WHY: Routing never hangs.
  UNSURE: This is keyword matching. It is acceptable only because both guards still run on that path. Known misroute: "I don't want to return it, just tell me where it is" goes to return_refund.

[2026-10-19 11:14] DECISION: The pipeline fails closed on input-guard error; router failure degrades to escalation (still output-guarded); handler or output-guard failure/timeout returns a constant safe fallback, never the draft. Blocked turns are not written to session history.
  WHY: "Never raise, never leak an unguarded draft." Keeping blocked text out of history keeps injection payloads out of later prompts.
  ABANDONED: A single global timeout around the turn. A slow router ate the whole budget and the escalation fallback had 0 s (caught by test_slow_router_degrades_within_budget). Each stage now gets a capped slice.
  UNSURE: The 30 s turn / 8 s router numbers are guesses until real LLM latency is measured.

[2026-10-19 11:35] DECISION: import-linter seal contract set to allow_indirect_imports = true.
  WHY: contracts.py imports the seal to verify it, so every package reached it indirectly and the contract failed for all of them, proving nothing. The property that matters is that no package outside guardrails can name the seal directly. Contract now: 1 kept, 0 broken.

[2026-10-19 11:35] DECISION: Moved TURN_TIMEOUT_S, ROUTER_CONFIDENCE_THRESHOLD, ORDERS_PATH, RAG_MIN_SCORE, KB_DIR, RAG_CACHE_DIR from os.environ reads into Settings.
  WHY: pydantic-settings reads .env but doesn't export it to os.environ, so these values in .env were silently ignored. Found when wiring the components together.

[2026-10-19 11:35] DECISION: Per-role asyncio.Semaphore in LLMClient (LLM_MAX_CONCURRENCY, default 32); queue wait recorded on the span.
  WHY: Without it, overload produces a retry storm against the model server; docs/scaling.md identified it as the missing piece.
  UNSURE: 32 is a placeholder until the server's real batch capacity is known. API-level admission control (503 + Retry-After) is still not implemented.

[2026-10-19 11:35] DECISION: The escalation handler returns a "reach a person" reply even if the handoff store is down.
  WHY: Before, a Redis outage made the escalation path fall through to the generic safe fallback, so the safest path was the one that failed.

[2026-10-19 11:35] DECISION: Keep Intent as a closed enum; adding billing_dispute means a one-line enum addition plus a handler and eval rows.
  WHY: A typed, closed set makes the router schema and eval labels explicit. A free-form string would let a typo'd intent route silently. The cost is one contract edit per new agent, which is the reviewable change we want.

[2026-10-19 11:06] DECISION: Input guard = normalisation + decoded views (leetspeak, de-spacing, base64, rot13, URL, Unicode tags) + ~25 weighted deterministic signals combined with noisy-OR (hard block 0.85, soft band 0.5) + an LLM classifier with per-call nonce delimiters.
  WHY: The brief rules out keyword matching. Weak cues customers really use ("waive", "asap", "you promised") stay below the block line alone but add up in combination.
  ALTERNATIVES CONSIDERED: A binary blocklist (false positives on real customers); LLM only (fails open in an outage, can be injected).
  ABANDONED: Collapsing whitespace before de-spacing merged "i g n o r e  y o u r" into one blob; mapping '1'→'i' everywhere broke "ru1es".
  UNSURE: Weights are hand-set against a set the same author wrote.

[2026-10-19 11:09] DECISION: Input-guard outage policy: soft-suspicious → block; clean → allow but marked checker="heuristic_only", which puts the output guard into strict mode. Hard-threshold blocks skip the LLM entirely.
  WHY: Blocking everyone in an outage turns an LLM incident into a total refusal; the soft band is where being conservative is cheap. An injected classifier can't argue a hard-pattern payload back to allow.

[2026-10-19 11:10] DECISION: Refund requests and waiver *requests* are allowed at input; medical red flags are never blocked at input. The output guard re-derives red flags from the message, puts the fixed safety message first, and strips other health statements.
  WHY: Asking is legitimate; only a human can grant an exception, and the output guard stops the bot from granting it. The output guard is the one place guaranteed to see every reply.
  UNSURE: Negated symptoms ("no chest pain") still trigger the safety message, kept on purpose since a miss costs more.

[2026-10-19 11:12] DECISION: Output guard: in refund context a money amount must match a CONFIRMED tool field (a price in a retrieved passage is not enough); a policy duration passes only if it is in a retrieved passage AND stated with its inspection condition. Remediation is a deterministic rewrite that re-states any confirmed fact it removed, never LLM regeneration.
  WHY: "₹3,499" is in the price list, but "you'll get ₹3,499 back" is still a promise. A deterministic rewrite can't introduce a new claim, and re-stating confirmed facts means an over-eager rubric can't erase them. That is the "neither side silently overrides" half of the reconciliation.
  ABANDONED: Re-stating every confirmed fact, which duplicated facts the kept sentences already had.
  UNSURE: The unconfirmed estimate is removed even when hedged; may be stricter than a reviewer expects.

[2026-10-19 11:18] DECISION: Guard heuristic misses (authority claim without trigger words, insider hearsay, acrostic exfiltration, Hinglish) were not patched with new keywords.
  WHY: Patching them would fit the heuristic numbers to our own eval set. Heuristic-only result on the PulseNest adversarial set: recall 20/24, false-positive rate 1/18; output deterministic check 14/15.

[2026-10-19 11:45] DECISION: Fixed the RAG LLM-outage fallback after the first end-to-end smoke run quoted the E5 (Bluetooth) section for an E2 question. Now: search all retrieved passages, ignore stopwords and title words, weight terms by rarity across sections, require identifier tokens (E2, model numbers) to match exactly, pick sentences by match, and return no answer when nothing matches.
  WHY: An answer that cites a real article but is about the wrong error code looks grounded and isn't. That is worse than "I don't know".
  ABANDONED: (1) plain word overlap on the top passage (E5 for E2); (2) rarity weighting on the top passage only (top chunk was E3-E5, no E2 at all); (3) all passages without the identifier rule (generic words like "monitor" pulled in other articles).
  UNSURE: Still a heuristic. It can drop context a quoted sentence refers to ("Express is not available elsewhere" without the city list).

[2026-10-19 11:50] DECISION: Circuit breaker in LLMClient (3 consecutive failed calls → refuse calls for 15 s → one half-open probe). 4xx errors don't count.
  WHY: With the endpoint down, every LLM call spent seconds retrying a refused connection, so turns took 5-25 s before reaching their deterministic fallbacks. 4xx is excluded because the tool agent's 400 (no tool parser on the server) would otherwise have opened the circuit and disabled the router and guards too. Caught while wiring the breaker against the live server.

[2026-10-19 12:05] DECISION: In-house server serves Qwen3.8-27B-AWQ-INT4 (8192 ctx). Send chat_template_kwargs.enable_thinking=false and strip any "...</think>" prefix from content defensively.
  WHY: Plain chat replies started with the model's reasoning ("We need to reply with the word OK...") and took 2.8 s vs 0.37 s with thinking off. Without this the RAG agent would have shown customers its chain of thought.
  UNSURE: Groq/other providers may reject the extra_body field; LLM_DISABLE_THINKING=false turns it off.

[2026-10-19 12:15] DECISION: Removed the free-text rationale from the router's LLM schema; input-guard reason capped at 6 words; input-guard LLM timeout raised 4 s → 6 s; embedding model warmed up at startup.
  WHY: Live traces show decode at ~100 ms/token and output tokens dominate latency (guard 30-48 tokens = 2.5-3.9 s, router 54 tokens = 1.7 s). The rationale sentence was unused. The guard's measured latency was too close to its 4 s timeout, which would silently drop it to heuristic-only. The first live turn failed because the embedder loaded lazily (14 s) inside the handler's budget.
  RESULT: Smoke turns went from 8-25 s (one failed) to 6.6-11.6 s, all correct.
  UNSURE: Input guard and router still run sequentially (~4.5 s before a handler starts). Running the router speculatively in parallel would cut ~2 s but would let the router see unchecked text, weakening the structural guarantee. Not done.

[2026-10-19 12:15] FINDING: The in-house vLLM server isn't started with --enable-auto-tool-choice --tool-call-parser hermes, so native tool calling returns 400. The tool agent falls back to the deterministic tool call plus a templated answer from confirmed fields (correct, but not LLM-phrased).

[2026-10-19 12:40] DECISION: Shipped defaults point at Groq (llama-3.3-70b-versatile for agent and guard, llama-3.1-8b-instant for the router, json_object structured output, thinking switch off). The self-hosted vLLM setup used during development remains a .env change.
  WHY: The brief specifies Groq. The router is a short classification, so the 8B model is faster, cheaper and has higher free-tier limits; the guard stays on the 70B because a missed injection costs more than a slower turn. json_object rather than json_schema because Groq's docs don't state json_schema support for these models, and every output is validated with pydantic anyway.
  UNSURE: Not yet run against Groq. Routing accuracy and guard recall were only measured on the self-hosted model, and they need re-measuring on the Groq models before any number is reported.
