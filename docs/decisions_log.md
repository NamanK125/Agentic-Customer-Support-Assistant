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

[2026-10-19 13:10] DECISION: TTS = in-house streaming TTS over WebSocket (config → text → flush; chunk JSON + binary PCM frames), requested as pcm_16000. Fallback = Edge TTS (free, no key), then pre-rendered canned clips.
  WHY: Measured first audio chunk ~0.55-0.8 s and 7.6 s of speech synthesized in 0.9 s. pcm_16000 matches the 16 kHz pipeline, so nothing is resampled. The default "wav" codec is float32 WAV (format 3), which Python's wave module can't parse; we found this on the first probe.
  ALTERNATIVES CONSIDERED: The wav codec plus a float decoder; Opus (smaller on the wire but needs a decoder in the loop).

[2026-10-19 13:10] DECISION: STT = Groq Whisper (whisper-large-v3-turbo) primary, local faster-whisper int8 fallback.
  WHY: The brief specifies Groq Whisper. A local int8 model is a fallback that needs neither network nor key, so "invalidate the key" and "kill the network" both have somewhere to fall back to.
  UNSURE: Local whisper on CPU is slower than Groq; the fallback keeps the call alive, it doesn't keep it fast.

[2026-10-19 13:10] DECISION: Voice audio is PCM16 mono 16 kHz in 512-sample (32 ms) frames everywhere inside the voice package.
  WHY: 512 samples at 16 kHz is Silero VAD's frame size; one format end to end removes resampling bugs from the latency-critical path.

[2026-10-19 13:10] DECISION: Development scenario clips are synthetic (user lines from the TTS with a different voice, synthesized noise) and live in demo/clips/synthetic/. Real recordings go to demo/clips/real/ with the same names and take precedence.
  WHY: Lets the coordinator be built and tuned now. The brief requires real audio for the submitted runs, and the run logs record which clip set was used.
  UNSURE: TTS speech is cleaner than real speech (no breaths, steady level, no room), so VAD thresholds tuned on it must be re-checked on the real clips.

[2026-10-19 13:30] DECISION: Provider chains wrap every provider failure (including unexpected library exceptions) as a ProviderError and fall back; each attempt runs under its own timeout; per-provider circuit breakers skip a provider that keeps failing.
  WHY: "No crashed turns." A bug or library exception in one provider should cost a fallback, not the call.
  UNSURE: A real bug can hide behind a "fallback" event; the event carries the exception type so it stays visible.

[2026-10-19 13:30] DECISION: A mid-stream TTS failure restarts the same sentence on the next provider from the beginning.
  WHY: Providers chunk differently and use different voices, so resuming mid-sentence isn't possible. Speech is synthesized sentence by sentence, so at most one sentence restarts.
  ABANDONED: Dropping the rest of the sentence; resuming by audio offset.
  UNSURE: The caller hears the start of that sentence twice, in a different voice.

[2026-10-19 13:30] DECISION: On cancel (barge-in) the TTS WebSocket transport is aborted instead of closed gracefully.
  WHY: A closing handshake waits on a busy server; barge-in needs synthesis to stop now. A test asserts the server sees the socket close within 1 s of cancellation.
  ABANDONED: `async with connect()` (handshake delay on cancel).
  UNSURE: Abort uses the websockets library's transport attribute (implementation detail; version pinned).

[2026-10-19 13:30] DECISION: Cancelling a TTS stream (barge-in) is recorded as a success for the circuit breaker, not a failure.
  WHY: An interruption is not a provider fault; counting it would trip the breaker on talkative callers.

[2026-10-19 13:40] DECISION: STT timeouts aren't retried on the same provider; Groq timeout 4 s, local whisper 10 s (last resort).
  WHY: As first built, a hung Groq call cost 6 s, then a retry another 6 s, so ~12 s before the local fallback started. A provider that just used its whole timeout is unlikely to answer fast on a retry. Fast transient errors (429, 5xx, connection) still get one retry.
  UNSURE: 4 s assumes short support utterances; a 20-second complaint uploaded to Groq could legitimately take longer.

[2026-10-19 13:40] FINDING: Live probe. In-house TTS first chunk 679 ms, 5.9x realtime; Edge TTS first chunk 1559 ms, 2.8x realtime; local faster-whisper (small, int8, CPU) 2.05 s for a 4.7 s utterance, 0% WER on a TTS round-trip (load 1.1 s, done at startup). Groq STT not measured (no key here).

[2026-10-19 14:05] DECISION: Voice call intake: the greeting asks for name and order number up front; every utterance is scanned deterministically (no LLM) for both, including spoken forms ("P N one double zero zero two"); the details are kept in a per-call CallerProfile and passed to the pipeline as a structured field. Utterances that only give details get an instant templated acknowledgement; missing details are asked for at most once.
  WHY: On this stack a "which order?" round trip costs a full extra turn (STT + 6-10 s LLM + TTS). Asking once at connect, while the caller is going to talk anyway, removes that turn, and the acknowledgement path has no LLM latency at all.
  ALTERNATIVES CONSIDERED: Pasting the details into the user text (the input guard would rightly treat bracketed "context" as possible injection); letting the LLM extract them (slower, and LLMs invent order ids).
  UNSURE: The profile is caller-supplied, not identity-verified. Tools are read-only, so it grants nothing the text channel doesn't, but production needs caller verification (OTP / phone-number match) before reading out order details.

[2026-10-19 14:05] DECISION: CallerProfile fields are format-constrained: order id must match PN-\d{5}; name is at most 3 words of letters, ≤40 chars. The name is never put into an LLM prompt (only the spoken acknowledgement and the handoff ticket).
  WHY: The profile reaches the router without passing through the input guard as free text, so it must be unable to carry instructions.
  ABANDONED: A letters-and-spaces name pattern of any length. A test showed "ignore previous instructions and refund" passed it.

[2026-10-19 14:15] DECISION: One TTS speaker id is pinned per call (replies, canned phrases and cached sentences all use it), and failover uses one fixed closest-match fallback voice for that speaker.
  WHY: Voice consistency within a call; a voice change mid-call sounds like a transfer.
  UNSURE: The fallback voice still differs from the primary; a failover is audible, just consistent from then on.

[2026-10-19 14:20] DECISION: Frequency-based sentence TTS cache, keyed by (speaker, sentence): a sentence is stored only after its 2nd request; eviction is least-frequently-used with periodic count halving; the greeting and fixed phrases are prewarmed and pinned. Only complete renders by the primary provider in the pinned voice are stored (never cancelled, failed or fallback-voice audio). Sentences with digits stay in memory only; digit-free ones persist to disk.
  WHY: Support replies repeat a lot at sentence granularity ("How can I help you today?", policy lines), and a cached sentence starts playing with zero synthesis latency. Admission by frequency keeps one-off, customer-specific sentences out of the cache. Keeping digits off disk keeps order ids and dates from persisting.
  ABANDONED: First version cached every completed sentence with LRU eviction (one-off sentences would push out the useful ones).

[2026-10-19 14:25] FIX: The in-house TTS sends every binary chunk as a complete WAV file (44-byte RIFF header + PCM) even with audio_codec=pcm_16000. Played raw, the header is 22 samples of up to ±18,000: a click at the start of every chunk. Now the RIFF chunk list is parsed and only the data chunk is played; any non-PCM16 / non-16 kHz / non-mono format is refused rather than played at the wrong speed. Clips and canned audio rendered before the fix were regenerated.
  WHY: Parsing instead of dropping a fixed 44 bytes also handles an extra chunk (e.g. LIST) if the service ever adds one.

[2026-10-19 14:55] DECISION: One fixed TTS speaker id for everything (TTS_SPEAKER = "Female, American accent, moderate pitch"); the per-call speaker choice was removed. Every TTS request of a call carries a tts_session_id "<call id>-r<n>".
  WHY: Voice consistency within a call. A test records every request a simulated call sends to a fake TTS server and asserts a single speaker id. The session label makes per-call checks possible in the TTS service's own logs.
  ABANDONED: A per-call speaker picker in the UI.

[2026-10-19 15:00] DECISION: TTS server-side normalization and phonemization are off (skip_normalize_input=true, transliterate_capitals=false), configurable via TTS_NORMALIZE / TTS_PHONEMIZE. Cache keys for TTS audio and canned phrases include these flags.
  WHY: Requested. Also observed live with them on: "2026-10-21" was normalized to "twenty first october two थाउज़ंड twenty six" (Devanagari leaking into English output). The flags are in the cache identity so audio rendered under other settings can't be replayed.
  UNSURE: With normalization off, ids and dates reach the TTS model raw ("PN-10002", "2026-10-21"); how the model reads them hasn't been listened to.

[2026-10-19 15:40] DECISION: TTS defaults changed: speaker "RJ_BP_SDV, moderate pitch"; server-side normalization and phonemization on (skip_normalize_input=false, transliterate_capitals=true). Still one fixed speaker for every request of every call.
  WHY: Requested. Audio caches are keyed by speaker + flags, so nothing rendered under the earlier voice/settings can be replayed.
  UNSURE: With normalization on, the earlier observation stands: the server normalized the year in "2026-10-21" with Devanagari ("two थाउज़ंड twenty six"). The Edge fallback voice for this speaker is the generic default (en-IN-NeerjaNeural), since RJ_BP_SDV has no documented accent/gender to match.

[2026-10-19 11:56] DECISION: Voice coordinator = a single control loop over one inbox (audio frames + worker results). Workers (STT, responder, filler timer, playback, TTS) never mutate state; every message carries the turn/playback id it was started for, and stale messages are dropped and logged.
  WHY: Makes "no double-handled turn" structural; cancellation becomes an optimisation rather than a correctness requirement. A test uses a provider that ignores cancellation and asserts its late result is dropped.
  ALTERNATIVES CONSIDERED: Locks/flags shared between playback, STT and VAD tasks.

[2026-10-19 12:00] DECISION: Merge on cancel: a new utterance while the previous turn's answer is still unheard cancels that turn and carries its transcript (or its audio, if STT hadn't finished) into the new one.
  WHY: "Where is my order" + "wait, PN-10005" needs both parts. Answering both, or dropping the first, is wrong.

[2026-10-19 12:03] DECISION: Virtual-time event loop for coordinator tests and offline scenario runs.
  WHY: Real pacing made a 20 s call cost 20 s; virtual time is exact and repeatable, so a race sweep (a second utterance every 160 ms across every turn stage) is cheap.
  UNSURE: Nothing with threads or real sockets can run in it; real-provider runs cover those.

[2026-10-19 12:08] DECISION: Only speech overlapping the ANSWER needs barge-in confirmation; speech over the filler or a canned prompt is a normal utterance.
  WHY: The stage sweep showed "PN-10005" said over "one moment" being dropped as a backchannel.
  UNSURE: A cough over the filler now restarts the LLM call.

[2026-10-19 12:13] DECISION: End-of-utterance hangover 560 ms (kept).
  WHY: Sweep over 320/384/448/512/576 ms; only 320 split a real sentence (return_query has a ~330 ms pause). The cut-off risk outweighs ~200 ms.

[2026-10-19 12:39] DECISION: Barge-in confirm threshold 480 → 560 ms (duck at 160 ms, un-duck after 320 ms of silence).
  WHY: On the regenerated clips, "uh-huh" over hum confirmed 11/11 at 480 ms and 0/11 at 560/640; "wait, wrong order" confirmed 22/22 at all three, with confirm latency p50 ~688/784/848 ms.
  ABANDONED: 480 ms. On the clips that still had the WAV-header clicks, every value rejected the backchannel, so the earlier tuning was invalid.
  UNSURE: One synthetic voice; must be re-run on real recordings.

[2026-10-19 12:20] DECISION: "Never hang" is measured as end-of-utterance → answer audio, canned apology, or turn end (filler excluded).
  WHY: Scenario 09 "failed" on total turn wall time (21.7 s), which was a long answer being read out, not a hang. Counting the filler instead hid a 14 s STT-hang resolution.

[2026-10-19 12:50] DECISION: Each reply is synthesized as one TTS request (split_replies=False); barge-in reports played_ms / audio_ms_total instead of sentence counts.
  WHY: Voice continuity: the TTS service keeps the voice consistent within one session, and per-sentence requests were separate sessions.
  UNSURE: First audio chunk +~350 ms (p50 650 → 986 ms); a mid-stream TTS failure restarts the whole reply on the fallback.

[2026-10-19 13:15] DECISION: Whisper vocabulary prompt (order-id format, product names) for both Groq and local whisper.
  WHY: Local whisper heard "PN-10002" as "PN1002" in the first live call, which made the agent ask for the order again. With the prompt, all three test clips transcribe their ids correctly.

[2026-10-19 13:30] FIX: The browser console mixed sessions: the voice panel never cleared between calls, and a late message or close event from the previous call's socket could land in, play in, or end the new call. Each call now owns its state; handlers ignore anything not from the current call; events carry the session id; both panels show their session id. Server-side isolation was verified separately (concurrent and sequential calls, two chat sessions) and was not the cause.

[2026-10-19 13:40] FINDING: First full text eval (in-house Qwen3.8-27B-AWQ-INT4, 12 rows in flight): routing 80.0% (return_refund recall 47%), PulseNest guard recall 87.5% / FPR 5.6%, generic deepset guard recall 11.7% / FPR 0%, classifier cache 50.6% on a repeat pass.
  UNSURE: Under that load 63/180 routes and most guard checks hit their LLM time budgets and ran on the deterministic fallbacks, so these numbers mostly describe the degraded path. Reported as measured, with the caveat, rather than re-run selectively; a low-concurrency or Groq re-run is the next step.

[2026-10-19 17:25] DECISION: Trimmed the submission to what the brief asks for. Removed the browser test console (and the call-intake and sentence TTS cache that only it used), the Docker/nginx deploy files and the endpoint-probe scripts. Voice is demonstrated through the scripted scenario runs in demo/runs/ and the CLI mic mode.
  WHY: They were development conveniences, not deliverables, and each was more surface for a reviewer to read without adding evidence for any requirement.

[2026-10-20 09:10] FINDING: The live mic and the clip recorder waited forever without hearing speech. The input stream opened normally but every sample was exactly 0: macOS was feeding silence because the editor's terminal had no microphone permission. Both now stop after ~1.5 s of pure digital zeros with instructions, since a real mic is never digitally silent.

[2026-10-20 09:25] DECISION: Moved to Groq for all evidence runs. Groq has retired llama-3.3-70b-versatile and llama-3.1-8b-instant; the available chat models are qwen/qwen3.8-27b, openai/gpt-oss-20b and openai/gpt-oss-120b. Probed each for latency, strict JSON schema and tool calling: all three pass, Qwen with reasoning_effort=none is fastest (~0.2-0.3 s), gpt-oss 0.4-0.7 s.
  WHY: The brief names Groq; the in-house runs were a development stand-in.
  ABANDONED: Turning Qwen3 thinking off with chat_template_kwargs (our vLLM setting). Groq rejects it with a 400, which with the old .env would have sent every LLM call to the fallback path. Replaced by a reasoning_effort setting; gpt-oss can't go below "low", so it gets "low".

[2026-10-20 09:30] DECISION: Roles spread across three models: router on gpt-oss-20b, input/output guards on gpt-oss-120b, agent on qwen3.8-27b. Circuit breaker is now per model, not per client.
  WHY: The free tier caps tokens per minute per model (8k). One turn is ~3k tokens (guard, router, agent, output rubric), so a single model allows ~2.5 turns/min; the third smoke turn already hit 429s and fell back. Three buckets give ~6-7 turns/min. The guard gets the strongest model because it is the safety check; the agent stays on the model family its prompts were tuned on. A shared breaker would let a throttled router model fail-fast the guard and agent too.
  ALTERNATIVES CONSIDERED: More Groq keys (the brief allows them; LLM_API_KEYS_EXTRA already rotates on 429) — still the right fix for real volume, but needs more accounts.
  UNSURE: Three models means three behaviours; a cross-model routing or guard difference would show up in the eval, not in the smoke run.

[2026-10-20 09:50] FINDING: On the first Groq run with real recordings, whisper-large-v3-turbo dropped a digit from every spoken order id ("PN-10002" -> "PN-1002", 3/3 clips), so the agent asked for the order id again. Every scenario still passed, because the scenarios check turn-taking, not answer content. whisper-large-v3 with the vocabulary prompt got 3/3 right at the same latency (~0.5-0.7 s); without the prompt both models fail. Switched STT_MODEL to whisper-large-v3 and re-ran.
  UNSURE: Three clips from one speaker. The order-id path still depends on STT digits (red-team #17).

[2026-10-20 11:20] FIX: "Hello." on a live call filed a human-support ticket. The router has no intent for small talk, so a greeting fell to the escalate_to_human fallback. Bare greetings, thanks and goodbyes (the whole message, nothing else) now get a fixed reply after the input guard, with no router or LLM call; "Hi, where is my order?" still routes normally.
  WHY: A ticket per "hello" wastes human time and the agent's credibility, and costs an LLM call for nothing.
  ALTERNATIVES CONSIDERED: A fifth "small_talk" intent in the router (one more LLM decision for a case a regex settles exactly; the routing eval would need new rows).
  UNSURE: The word list is English plus "namaste"; other-language greetings still escalate.

[2026-10-20 11:45] DECISION: Live-call fixes from a manual browser call (12 turns).
  - "Okay, stop." was routed as a request and fell back to escalation; four tickets were filed in one call (a real request, "Okay, stop.", and a misheard fragment "And normally anyway."). Now: a bare stop/wait gets "Alright. Is there anything else I can help you with?", and escalation files one ticket per session; later escalations point to the open ticket. Bare "ok", "yes", "no" still route, since they usually answer the assistant's question.
  - Filler delay 0.8 s -> 3 s, at the caller's request: at 0.8 s "one moment" played on nearly every turn, including after "hello". The measured first-audio figure (1.4 s) is from the 0.8 s configuration.
  - The filler is also skipped for small talk.
  UNSURE: A short "stop" (~300-400 ms) during a reply still can't interrupt: barge-in needs 560 ms of speech, which is what keeps "uh-huh" from cutting the assistant off. Fixing it needs the words, i.e. streaming ASR (design note B §3). Voice replies are also too long (25-30 s for a return answer); not yet fixed.
