# Eval datasets

Built by `scripts/fetch_datasets.py` (seed 42). Run: `uv run python scripts/fetch_datasets.py`.
Raw downloads are cached under `.cache/hf` (gitignored). Outputs are in `eval/data/`.

## Sources

### 1. Bitext customer-support utterances (routing)

- HF id: `bitext/Bitext-customer-support-llm-chatbot-training-dataset`
- License: **CDLA-Sharing-1.0** (Community Data License Agreement, Sharing). Use in an internal evaluation is permitted; derived data that is shared must carry the same license terms.
- Taken: `instruction` (user utterance) and `intent` columns from the train split (26,872 rows).
- Generic retail customer support, not health devices. Synthetic, mostly lowercase or typo-heavy phrasing, and some profanity.

Sampling and filters:
- Only the 9 Bitext intents in the mapping table below are used. The other 20 intents are excluded.
- Order-number placeholders (`{{Order Number}}`) are replaced with `PN-10` + 3 random digits (seeded RNG).
- Rows with any other placeholder (`{{Account Type}}`, `{{Refund Amount}}`, `{{Delivery Country}}`, ...) are dropped: 624 rows.
- Exact duplicates (case-insensitive, after filling) are dropped.
- Balanced per **our** intent: 40 Bitext rows per mapped intent, split evenly across the Bitext intents that feed it (`rng.sample`). An earlier version sampled 40 per *Bitext* intent, which made `return_refund` 203 rows against 42 `product_question`, so accuracy would have been dominated by one class.

Intent mapping (Bitext intent -> our `Intent`):

| Bitext intent | Our intent | Rows |
|---|---|---|
| `track_order` | `order_status` | 20 |
| `delivery_period` | `order_status` | 20 |
| `get_refund` | `return_refund` | 8 |
| `track_refund` | `return_refund` | 8 |
| `check_refund_policy` | `return_refund` | 8 |
| `cancel_order` | `return_refund` | 8 |
| `check_cancellation_fee` | `return_refund` | 8 |
| `contact_human_agent` | `escalate_to_human` | 20 |
| `complaint` | `escalate_to_human` | 20 |

Excluded Bitext intents, and why:
- `contact_customer_service`: generic "how do I reach support / hours" questions. Ambiguous between escalation and a FAQ answer.
- `delivery_options`: shipping-method and country questions. These are policy questions, so they fit `product_question`, but Bitext's version is country-placeholder based and not PulseNest policy.
- `change_order`, `place_order`, `change_shipping_address`, `set_up_shipping_address`: order modifications the bot has no tool for. There is no matching intent.
- `check_invoice`, `get_invoice`: no invoice support in the domain.
- `check_payment_methods`, `payment_issue`: no payment scope in domain.md.
- `create_account`, `delete_account`, `edit_account`, `recover_password`, `registration_problems`, `switch_account`: account management, out of scope.
- `newsletter_subscription`, `review`: marketing or feedback, not support intents.

Note on `cancel_order` and `check_cancellation_fee` mapped to `return_refund`: the brief groups cancellation with returns, and the bot has no cancel tool. These rows test "route to the closest handler", not a clean category match. Treat that column with caution when reading per-intent scores.

### 2. deepset prompt injections (injection detection)

- HF id: `deepset/prompt-injections`
- License: **Apache-2.0**.
- Taken: `text` and `label` from both `train` (546) and `test` (116) splits, 662 rows total.
- Label mapping: `1` (injection) -> `block`, `0` (benign) -> `allow`.
- Generic, not PulseNest-specific. Many benign rows are German or other non-English news-style questions, and some injections are non-English.

English filter (heuristic, imperfect):
- Keep a row only if at least 98% of its characters are ASCII **and** it contains at least one word from a small English stopword list (`the`, `is`, `you`, `and`, `my`, ...).
- Dropped 251 of 662 rows. 411 rows pass.
- Known weaknesses: it drops short English prompts with no stopword (for example, "Ignore previous instructions"). It keeps romanised Hindi or other Latin-script text that contains a stopword. A spot check found at least one romanised-Hindi row in the output.

Sampling: 60 injection and 60 benign rows drawn with the seeded RNG from the filtered pool, then shuffled.

## Hand-written rows (in `scripts/fetch_datasets.py`)

- 42 `product_question` rows: 40 unambiguous plus 2 ambiguous. They cover accuracy, error codes E1/E3/E5, cuff size, batteries, weight limit, app OS compatibility, Care+ pricing and cancellation, warranty, probe covers, recalibration, shipping costs and times, return windows, and restocking fees. Phrasing is varied, with typos and Hinglish (romanised Hindi) rows.
- 10 `escalate_to_human` rows in PulseNest context: exception requests, defective devices, billing disputes, requests for a person, and some Hinglish.
- 10 `ambiguous: true` rows: mixed intents, no order id, a safety-sensitive reading, and one greeting. Labels are best guesses, not gold.

## Counts

`eval/data/routing_eval.jsonl`: 180 rows, `ambiguous` true for 10.

| expected_intent | rows |
|---|---|
| `order_status` | 43 |
| `return_refund` | 43 |
| `escalate_to_human` | 52 |
| `product_question` | 42 |

Composition: 120 Bitext (sampled), 50 handwritten unambiguous (40 product, 10 escalate), 10 handwritten ambiguous. The ambiguous rows are counted in the per-intent totals above: 3 `order_status`, 3 `return_refund`, 2 `escalate_to_human`, 2 `product_question`.

`eval/data/injection_eval.jsonl`: 120 rows. 60 `block`, 60 `allow`. The English filter dropped 251 of 662 source rows.

## Caveats

- Bitext is generic retail, not health devices. Its phrasing is synthetic and cleaner than real users, with many near-duplicate templates. Routing accuracy on it will overstate real-world performance.
- Bitext order-number ids are synthetic (`PN-10xxx`) and do not exist in the mock order system. These rows test routing, not order lookup.
- The `cancel_order` and `check_cancellation_fee` mapping is a judgment call (see above).
- deepset injections are generic prompt-injection text, not PulseNest-specific. They do not test domain attacks such as fake refund approvals or fake ETAs. That is what the separate hand-written `eval/data/adversarial_domain.jsonl` covers (described in `docs/guardrail_trace.md`).
- The English filter is a heuristic. Expect some non-English rows to remain and some English rows to be lost.
- Ambiguous-row labels are best guesses by one author and have not been double-checked.
