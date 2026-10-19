"""Deterministic input signals. These are FEATURES with weights, not the decision.

Each signal is a phrase pattern with a weight in [0, 1]. Signals from all text views are combined
with noisy-OR, so two weak signals (say, a staff-authority claim plus a "process it now"
directive) add up to a strong one while either alone stays below the block threshold. Weights
were set by hand against eval/data/adversarial_domain.jsonl; they are not learned.

Deliberately weak (< soft threshold) on their own, because real customers say these things:
asking for a waiver, "process my refund asap", "you promised", "forget the policy".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from support_assistant.contracts import ChatTurn
from support_assistant.guardrails._text import TextViews, build_views

_F = re.IGNORECASE | re.MULTILINE


@dataclass(frozen=True)
class Signal:
    name: str
    category: str
    weight: float
    view: str = "plain"


@dataclass(frozen=True)
class SignalReport:
    signals: tuple[Signal, ...]
    score: float  # noisy-OR of the strongest weight per signal name
    views: TextViews

    @property
    def top_category(self) -> str | None:
        if not self.signals:
            return None
        return max(self.signals, key=lambda s: s.weight).category

    def names(self) -> list[str]:
        return sorted({f"{s.name}@{s.view}" for s in self.signals})


_INSTR = r"(?:instructions?|prompts?|rules|guidelines|directives|programming|guardrails?|constraints|restrictions|training)"  # noqa: E501
_STAFF = r"(?:manager|supervisor|admin(?:istrator)?|staff|employee|team\s*lead|developer|engineer|ceo|founder|support\s+agent|cs\s+lead|warehouse\s+(?:head|lead|manager))"  # noqa: E501

# (name, category, weight, pattern)
_PATTERNS: list[tuple[str, str, float, re.Pattern[str]]] = [
    # --- instruction override / injection -------------------------------------------------
    (
        "override_instructions",
        "prompt_injection",
        0.9,
        re.compile(
            rf"\b(?:ignore|disregard|forget|override|bypass|drop|discard)\b[^.\n]{{0,40}}?"
            rf"\b(?:previous|prior|above|earlier|all|your|the|system|any|these|those|existing)\b"
            rf"[^.\n]{{0,20}}?\b{_INSTR}\b",
            _F,
        ),
    ),
    (
        "override_policy",
        "unauthorized_action",
        0.45,
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,25}\b(?:the\s+|your\s+)?"
            r"(?:policy|policies|return\s+rules|terms)\b",
            _F,
        ),
    ),
    (
        "persona_switch",
        "prompt_injection",
        0.8,
        re.compile(
            r"\b(?:you\s+are\s+now|you(?:'re|\s+are)\s+no\s+longer|new\s+(?:instructions|rules|role|persona)\s*:|"
            r"developer\s+mode|dan\s+mode|jailbreak|do\s+anything\s+now|unrestricted\s+mode|"
            r"god\s+mode|sudo\s+mode)\b",
            _F,
        ),
    ),
    (
        "from_now_on",
        "prompt_injection",
        0.4,
        re.compile(r"\bfrom\s+now\s+on\b[^.\n]{0,30}\byou\b", _F),
    ),
    (
        "chat_template_tokens",
        "prompt_injection",
        0.95,
        re.compile(
            r"<\|im_(?:start|end)\|>|<\|(?:system|assistant|user|endoftext)\|>|\[/?inst\]|<<sys>>|"
            r"^\s*(?:system|developer)\s*:|\[(?:system|admin|developer|assistant)\]|"
            r"</?(?:system|admin|developer)>|#{2,}\s*(?:system|instruction|admin)",
            _F,
        ),
    ),
    ("fake_assistant_turn", "prompt_injection", 0.6, re.compile(r"^\s*assistant\s*:", _F)),
    (
        "fake_tool_output",
        "prompt_injection",
        0.8,
        re.compile(
            r"\b(?:tool|function|api|system)\s*(?:output|result|response|call)\s*[:=]|"
            r"[\"']?(?:refund_estimate_inr|confirmed_fields|restocking_fee_pct|return_window_ends|"
            r"tool_results?|function_call)[\"']?\s*[:=]|"
            r"\{\s*[\"'](?:eligible|tool|approved|status)[\"']\s*:",
            _F,
        ),
    ),
    (
        "fake_policy_quote",
        "prompt_injection",
        0.6,
        re.compile(
            r"\b(?:per|according\s+to|as\s+(?:stated|written)\s+in|under|section\s+\d+[.\d]*\s+of)\s+"
            r"(?:the\s+|your\s+|pulsenest(?:'s)?\s+)?(?:(?:new|updated|revised|internal|latest|"
            r"20\d\d|official)\s+){1,3}(?:return\s+|refund\s+)?(?:policy|policies|terms|sop|"
            r"guidelines|memo)\b"
            r"[^\n]{0,120}?\b(?:must|shall|required|entitled|automatic\w*|waive\w*|full\s+refund|"
            r"no\s+restocking|instant\w*)\b",
            _F,
        ),
    ),
    (
        "policy_update_claim",
        "prompt_injection",
        0.5,
        re.compile(
            r"\b(?:policy|memo|notice|sop)\s+(?:update|change|override|amendment)\b|"
            r"\bupdated\s+policy\s*:",
            _F,
        ),
    ),
    # --- system / tool extraction ---------------------------------------------------------
    (
        "system_prompt_ref",
        "system_extraction",
        0.85,
        re.compile(
            r"\b(?:system|initial|hidden|original|developer|internal|secret|pre-?)\s*"
            r"(?:prompt|instructions?|rules|notes|configuration|config)\b",
            _F,
        ),
    ),
    (
        "reveal_your_config",
        "system_extraction",
        0.6,
        re.compile(
            r"\b(?:reveal|show|print|repeat|output|display|tell\s+me|share|leak|dump|list|"
            r"what\s+(?:are|is|were))\b[^.\n]{0,30}\byour\s+(?:own\s+)?(?:prompt|instructions|rules|"
            r"guidelines|tools|functions|configuration|settings|internal)\b",
            _F,
        ),
    ),
    (
        "repeat_above",
        "system_extraction",
        0.85,
        re.compile(
            r"\b(?:repeat|print|output|copy|echo)\b[^.\n]{0,30}\b(?:everything|all|the\s+text|"
            r"words|content)\b[^.\n]{0,15}\b(?:above|before|so\s+far|verbatim)\b|\bverbatim\b",
            _F,
        ),
    ),
    (
        "internal_identifier",
        "system_extraction",
        0.5,
        re.compile(
            r"\b(?:get_order_status|check_return_eligibility|refund_estimate_inr|confirmed_fields|"
            r"json_schema|role\s*=\s*guard)\b",
            _F,
        ),
    ),
    (
        "tool_inventory",
        "system_extraction",
        0.5,
        re.compile(r"\bwhat\s+(?:tools|functions|apis)\s+(?:do|can)\s+you\b", _F),
    ),
    # --- unauthorized actions -------------------------------------------------------------
    (
        "authority_claim",
        "unauthorized_action",
        0.6,
        re.compile(
            rf"\b(?:as|i\s+am|i'm|im|this\s+is)\s+(?:the|a|an|your)?\s*(?:pulsenest\s+|store\s+|"
            rf"support\s+|customer\s+care\s+|senior\s+|regional\s+)?{_STAFF}\b",
            _F,
        ),
    ),
    (
        "authorization_token",
        "unauthorized_action",
        0.7,
        re.compile(
            r"\b(?:authori[sz]ation|override|approval|admin|employee|staff|manager)\s*"
            r"(?:code|id|key|token|pin|number|password)\b|\bi\s+(?:hereby\s+)?(?:authori[sz]e|approve)\b|"
            r"\b(?:pre-?approved|already\s+(?:been\s+)?approved|approved\s+by\s+(?:the\s+)?"
            r"(?:manager|supervisor|team))\b",
            _F,
        ),
    ),
    (
        "refund_now_directive",
        "unauthorized_action",
        0.45,
        re.compile(
            r"\b(?:issue|approve|process|authori[sz]e|grant|initiate|release|push|credit|transfer)\b"
            r"[^.\n]{0,25}\b(?:refund|money|amount|credit|payment)\b[^.\n]{0,40}"
            r"\b(?:now|immediately|right\s+(?:now|away)|today|without|asap|directly|manually|"
            r"no\s+questions|before\s+(?:the\s+)?inspection)\b",
            _F,
        ),
    ),
    (
        "skip_inspection",
        "unauthorized_action",
        0.6,
        re.compile(
            r"\b(?:skip|bypass|without|no\s+need\s+for|waive)\b[^.\n]{0,10}\b(?:the\s+)?"
            r"(?:inspection|verification|checks?|approval)\b",
            _F,
        ),
    ),
    (
        "waiver_request",
        "unauthorized_action",
        0.3,
        re.compile(
            r"\bwaive\w*\b|\bwaiver\b|\b(?:no|without|skip|remove|drop|cancel)\s+(?:the\s+|any\s+)?"
            r"restocking\b|\bmake\s+an?\s+exception\b",
            _F,
        ),
    ),
    (
        "mutate_record",
        "unauthorized_action",
        0.6,
        re.compile(
            r"\b(?:change|update|modify|set|edit|override)\b[^.\n]{0,30}\b(?:price|refund\s+amount|"
            r"order\s+total|eligibility|return\s+window|restocking\s+fee|refund_\w+)\b[^.\n]{0,25}"
            r"\b(?:to|as)\b|\bmark\b[^.\n]{0,25}\bas\s+(?:eligible|refunded|approved|defective|"
            r"delivered|unopened)\b",
            _F,
        ),
    ),
    (
        "treat_as",
        "unauthorized_action",
        0.55,
        re.compile(
            r"\b(?:treat|consider|count|record|log|classify|register)\b[^.\n]{0,30}\bas\s+"
            r"(?:unopened|defective|eligible|sealed|new|unused|within\s+(?:the\s+)?window)\b",
            _F,
        ),
    ),
    (
        "you_promised",
        "unauthorized_action",
        0.3,
        re.compile(r"\byou\s+(?:already\s+)?(?:said|promised|agreed|confirmed|told\s+me)\b", _F),
    ),
    # --- role-play framing ----------------------------------------------------------------
    (
        "roleplay_frame",
        "roleplay_manipulation",
        0.35,
        re.compile(
            r"\b(?:pretend|imagine|role-?play|let'?s\s+play|play\s+a\s+game|act\s+as|"
            r"hypothetically|in\s+a\s+story|simulate|stay\s+in\s+character)\b",
            _F,
        ),
    ),
    (
        "bot_persona",
        "roleplay_manipulation",
        0.5,
        re.compile(
            r"\b(?:you\s+are|you're|call\s+you|named?)\s+(?:now\s+)?[a-z]*bot\b|\brefund-?bot\b", _F
        ),
    ),
]

_ACTION_WORDS = re.compile(
    r"\b(?:refund|approve|waive|instructions|prompt|rules|fee|money|policy|exception)\b", _F
)
_CONTINUATION = re.compile(
    r"\b(?:as\s+(?:we\s+)?(?:agreed|discussed)|remember|you\s+(?:said|promised|agreed)|"
    r"continue|stay\s+in\s+character|go\s+ahead|now\s+do\s+it|do\s+it\s+now|like\s+before|"
    r"same\s+as\s+(?:before|above)|[a-z]*bot)\b",
    _F,
)

_OBFUSCATION_WEIGHTS = {
    "zero_width": 0.25, "unicode_tags": 0.6, "homoglyph": 0.35, "spaced_letters": 0.25,
}  # fmt: skip


def _noisy_or(weights: list[float]) -> float:
    p = 1.0
    for w in weights:
        p *= 1.0 - w
    return round(1.0 - p, 4)


# Run only on the "compact" view (letters only), for payloads split by spaces or punctuation.
_COMPACT = re.compile(
    r"(?:ignore|disregard|forget|bypass)(?:all|your|the|previous|prior|above|these)+"
    r"(?:instructions?|rules|prompts?|guidelines)|systemprompt|developermode|hiddeninstructions"
)


def _scan_views(views: TextViews) -> list[Signal]:
    found: dict[str, Signal] = {}
    for view_name, text in views.views.items():
        if view_name == "compact":
            if not found and _COMPACT.search(text):
                found["compact_override"] = Signal(
                    "compact_override", "prompt_injection", 0.85, "compact"
                )
            continue
        for name, cat, weight, pat in _PATTERNS:
            # views are ordered plain-first, so a signal is attributed to the plain view if it
            # fires there and to a decoded view only if it is hidden from the plain text
            if name not in found and pat.search(text):
                found[name] = Signal(name, cat, weight, view_name)
        if view_name == "plain" and "roleplay_frame" in found and _ACTION_WORDS.search(text):
            found["roleplay_with_action"] = Signal(
                "roleplay_with_action", "roleplay_manipulation", 0.6
            )
    out = list(found.values())
    hidden = [s for s in out if s.view not in ("plain", "leet", "despaced", "compact")]
    if hidden:
        out.append(Signal("payload_only_in_decoded_view", "obfuscation", 0.6, hidden[0].view))
    if any(s.view in ("leet", "despaced", "compact") for s in out):
        out.append(Signal("payload_only_in_folded_view", "obfuscation", 0.3))
    if any(k.startswith("base64") for k in views.views):
        out.append(Signal("base64_payload", "obfuscation", 0.25))
    for finding in views.obfuscation:
        if finding in _OBFUSCATION_WEIGHTS:
            out.append(Signal(finding, "obfuscation", _OBFUSCATION_WEIGHTS[finding]))
    return out


def _score(signals: list[Signal]) -> float:
    best: dict[str, float] = {}
    for s in signals:
        best[s.name] = max(best.get(s.name, 0.0), s.weight)
    # Obfuscation alone is not an attack (zero-width chars come from copy-paste), so it can
    # only amplify a real signal, never stand alone above the soft threshold.
    content = [w for n, w in best.items() if not _is_obfuscation(n, signals)]
    obf = [w for n, w in best.items() if _is_obfuscation(n, signals)]
    if not content:
        return min(_noisy_or(obf), 0.35)
    return _noisy_or(content + obf)


def _is_obfuscation(name: str, signals: list[Signal]) -> bool:
    return any(s.name == name and s.category == "obfuscation" for s in signals)


def scan(text: str, history: tuple[ChatTurn, ...] = (), history_turns: int = 3) -> SignalReport:
    """Score one message, optionally with recent user history for multi-turn setups."""
    views = build_views(text)
    signals = _scan_views(views)
    score = _score(signals)

    user_turns = [t.text for t in history if t.role == "user"][-history_turns:]
    if user_turns:
        hist_signals = [
            s
            for s in _scan_views(build_views("\n".join(user_turns)))
            if s.category != "obfuscation"
        ]
        if hist_signals:
            hist_score = _score(hist_signals)
            # A setup in an earlier turn matters when this turn continues it or carries its own
            # signal. Otherwise it decays, so one bad turn does not taint the whole session.
            factor = 0.7 if (signals or _CONTINUATION.search(views.views["plain"])) else 0.3
            signals.append(
                Signal(
                    "history_setup",
                    hist_signals[0].category,
                    round(hist_score * factor, 4),
                    "history",
                )
            )
            score = _score(signals)
    return SignalReport(signals=tuple(signals), score=score, views=views)
