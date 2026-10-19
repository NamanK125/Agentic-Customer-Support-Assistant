"""Build the routing and injection eval sets.

Run: uv run python scripts/fetch_datasets.py

Sources (see eval/DATASETS.md for licenses, mapping and caveats):
- bitext/Bitext-customer-support-llm-chatbot-training-dataset  -> routing_eval.jsonl
- deepset/prompt-injections                                    -> injection_eval.jsonl
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from pathlib import Path

from datasets import load_dataset

SEED = 42
ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / ".cache" / "hf"
OUT_DIR = ROOT / "eval" / "data"

BITEXT_ID = "bitext/Bitext-customer-support-llm-chatbot-training-dataset"
DEEPSET_ID = "deepset/prompt-injections"
# Balanced per *our* intent, split evenly across the Bitext intents that map to it.
PER_OUR_INTENT = 40
PER_INJECTION_LABEL = 60

# Bitext intent -> our Intent enum value. Only these are sampled.
BITEXT_MAP: dict[str, str] = {
    "track_order": "order_status",
    "delivery_period": "order_status",
    "get_refund": "return_refund",
    "track_refund": "return_refund",
    "check_refund_policy": "return_refund",
    "cancel_order": "return_refund",
    "check_cancellation_fee": "return_refund",
    "contact_human_agent": "escalate_to_human",
    "complaint": "escalate_to_human",
}

# Hand-written: no Bitext equivalent for PulseNest product questions.
PRODUCT_QUESTIONS = [
    "How accurate is the BP-200 cuff's pressure reading?",
    "wat is the warrenty on the BP200",
    "Does the Oxi O2 still give a reading with cold fingers?",
    "My Oxi O2 keeps showing poor readings, could nail polish be the cause?",
    "What does error E3 mean on the BP-200?",
    "The cuff shows E1 every time I start, what does that mean?",
    "what does E5 on the cuff mean, bluetooth wont pair",
    "Which arm size does the BP-200 cuff fit?",
    "Will the BP-200 fit an arm that is 45 cm around?",
    "How many readings can the BP-200 store for two users?",
    "What batteries does the BP-200 take and how long do they last?",
    "Is the Oxi O2 powered by AAA batteries?",
    "Which battery does the Therm T1 need?",
    "Can the Scale S3 take someone who weighs 170 kg?",
    "What is the maximum weight the S3 scale supports?",
    "Does the Care+ app work on Android 8?",
    "Will Care+ work on an iPhone running iOS 14?",
    "How many family profiles can I add on Care+?",
    "How much does Care+ cost per month and can I cancel anytime?",
    "If I cancel Care+ in the middle of the month, do I get a pro-rated refund?",
    "How long is the warranty on the BP-200?",
    "Is the Oxi O2 covered for 2 years or only 1 year?",
    "Are the Therm T1 probe covers reusable?",
    "Do I need to buy probe covers separately for the Therm T1?",
    "How often should the BP-200 be recalibrated, and is that free?",
    "What is the shipping charge if my cart is below 999 rupees?",
    "Is shipping free on the Scale S3?",
    "How long does standard delivery take to Jaipur?",
    "Is express shipping available in Bengaluru and how much does it cost?",
    "Can I return an unopened Oxi O2 within 30 days of delivery?",
    "What is the return window for an opened BP-200?",
    "If I open the Scale S3 and don't like it, is there a restocking fee?",
    "Can I return an opened Therm T1 thermometer?",
    "Can the BP-200 take readings without the app?",
    "Does Care+ include the PDF reports I can share with my doctor?",
    "Is the Therm T1 accurate to within 0.2 degrees?",
    "Mera Oxi O2 ka reading bahut kam aa raha hai, kya karu?",
    "BP-200 ka cuff kis size ke haath pe fit hota hai?",
    "Scale S3 ki warranty kitne saal ki hai?",
    "Care+ app iPhone pe chalegi kya, iOS version kitna chahiye?",
]

# Hand-written: PulseNest-context escalation requests.
ESCALATE_TO_HUMAN = [
    "I want to speak to a real person about my BP-200 exception request.",
    "Please connect me to a human agent, the bot is not helping.",
    "This is my third Oxi O2 that arrived defective. I want a manager on this now.",
    "Can someone from PulseNest support call me? I don't want a chatbot.",
    "I'm filing a formal complaint about the Scale S3, it arrived with a cracked display.",
    "bhai kisi insaan se baat karwao, bot kuch samajh nahi raha",
    "Mujhe support team se baat karni hai, Care+ ka bill galat laga hai.",
    "I need an exception on the restocking fee because I opened the cuff by mistake.",
    "Can a human agent tell me today whether my Therm T1 return is approved?",
    "The app sync has failed for weeks and I've complained twice. Escalate this please.",
]

# Hand-written tricky rows. expected_intent is a best guess; ambiguous=true.
AMBIGUOUS = [
    ("where's my order PN-10482 and can I return it if it's late?", "order_status"),
    ("where is my order", "order_status"),
    # Safety-sensitive reading: best guess is a human handoff (not a product answer).
    ("my BP-200 reading was 185/125 today, what should I do and can I return the cuff?", "escalate_to_human"),
    ("I want a refund for the Oxi O2 and also how do I pair it with the app?", "return_refund"),
    ("cancel my Care+ and refund last month's payment", "return_refund"),
    ("Is the Scale S3 safe with a pacemaker? I also want to return mine.", "product_question"),
    ("it's been two weeks and nobody has replied to my ticket, what now", "escalate_to_human"),
    ("order PN-10217 still isn't delivered and the thermometer is broken, sort it out", "order_status"),
    ("hi", "product_question"),
    ("I want to return my cuff, it doesn't fit my arm", "return_refund"),
]

# Small English stopword list used only by the language heuristic.
STOPWORDS = {
    "the", "a", "an", "is", "are", "to", "of", "and", "you", "your", "i", "it", "my",
    "what", "this", "that", "in", "for", "be", "not", "with", "on", "me", "do", "does",
    "please", "from", "they", "he", "she", "we", "all", "any", "now", "can", "will",
}


def is_english(text: str) -> bool:
    """Heuristic: >=98% ASCII characters and at least one English stopword.

    Imperfect: drops some short English prompts with no stopword, and keeps some
    romanised non-English text that happens to contain 'the', 'is', 'you', etc.
    """
    if not text:
        return False
    ascii_ratio = sum(ch.isascii() for ch in text) / len(text)
    words = re.findall(r"[a-z']+", text.lower())
    return ascii_ratio >= 0.98 and any(w in STOPWORDS for w in words)


def build_routing(rng: random.Random) -> list[dict]:
    from_hf = load_dataset(BITEXT_ID, split="train", cache_dir=str(CACHE_DIR)).to_list()

    pools: dict[str, list[str]] = {k: [] for k in BITEXT_MAP}
    seen: set[str] = set()
    dropped_placeholder = 0
    for row in from_hf:
        intent = row["intent"]
        if intent not in BITEXT_MAP:
            continue
        text = row["instruction"].strip()
        # Fill order-number placeholders with PN-10xxx ids; drop rows with any other placeholder.
        if re.search(r"\{\{(?!Order Number\}\})", text):
            dropped_placeholder += 1
            continue
        text = re.sub(r"\{\{Order Number\}\}", lambda _m: f"PN-10{rng.randint(0, 999):03d}", text)
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        pools[intent].append(text)

    sources: dict[str, list[str]] = {}
    for bitext_intent, ours in BITEXT_MAP.items():
        sources.setdefault(ours, []).append(bitext_intent)
    quota: dict[str, int] = {}
    for bitext_intents in sources.values():
        base, extra = divmod(PER_OUR_INTENT, len(bitext_intents))
        for i, bitext_intent in enumerate(sorted(bitext_intents)):
            quota[bitext_intent] = base + (1 if i < extra else 0)

    rows: list[dict] = []
    for intent in sorted(BITEXT_MAP):
        sampled = rng.sample(pools[intent], quota[intent])
        for text in sampled:
            rows.append({"text": text, "expected_intent": BITEXT_MAP[intent],
                         "source": "bitext", "source_intent": intent, "ambiguous": False})

    for text in PRODUCT_QUESTIONS:
        rows.append({"text": text, "expected_intent": "product_question",
                     "source": "handwritten", "source_intent": None, "ambiguous": False})
    for text in ESCALATE_TO_HUMAN:
        rows.append({"text": text, "expected_intent": "escalate_to_human",
                     "source": "handwritten", "source_intent": None, "ambiguous": False})
    for text, label in AMBIGUOUS:
        rows.append({"text": text, "expected_intent": label,
                     "source": "handwritten", "source_intent": None, "ambiguous": True})

    for i, row in enumerate(rows, start=1):
        row = {"id": f"route-{i:03d}", **row}
        rows[i - 1] = row
    print(f"routing: dropped {dropped_placeholder} Bitext rows with non-order placeholders")
    return rows


def build_injection(rng: random.Random) -> list[dict]:
    rows: list[dict] = []
    dropped_non_english = 0
    total = 0
    for split in ("train", "test"):
        for row in load_dataset(DEEPSET_ID, split=split, cache_dir=str(CACHE_DIR)).to_list():
            total += 1
            text = row["text"].strip()
            if not text or not is_english(text):
                dropped_non_english += 1
                continue
            rows.append({"text": text, "label": int(row["label"]), "split": split})
    print(f"injection: {total} rows, dropped {dropped_non_english} by English filter")

    out: list[dict] = []
    for label, expected in ((1, "block"), (0, "allow")):
        pool = [r for r in rows if r["label"] == label]
        for r in rng.sample(pool, PER_INJECTION_LABEL):
            out.append({"text": r["text"], "expected": expected,
                        "source": "deepset", "split": r["split"]})
    rng.shuffle(out)
    for i, row in enumerate(out, start=1):
        out[i - 1] = {"id": f"inj-{i:03d}", **row}
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    rng = random.Random(SEED)
    routing = build_routing(rng)
    injection = build_injection(rng)

    write_jsonl(OUT_DIR / "routing_eval.jsonl", routing)
    write_jsonl(OUT_DIR / "injection_eval.jsonl", injection)

    print("routing_eval.jsonl counts by expected_intent:")
    for label, n in sorted(Counter(r["expected_intent"] for r in routing).items()):
        print(f"  {label}: {n}")
    print(f"  total: {len(routing)} (ambiguous: {sum(r['ambiguous'] for r in routing)})")
    print("injection_eval.jsonl counts by expected:")
    for label, n in sorted(Counter(r["expected"] for r in injection).items()):
        print(f"  {label}: {n}")
    print(f"  total: {len(injection)}")


if __name__ == "__main__":
    main()
