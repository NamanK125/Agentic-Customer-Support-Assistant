"""Structural evidence check for drafts: the deterministic backbone of the output guardrail.

Every order-specific amount, date and eligibility verdict in a draft must be backed by a
ToolResult field listed in `confirmed_fields`. Generic policy terms (durations, percentages,
prices) must appear in a retrieved passage. The check works sentence by sentence so remediation
can drop exactly the offending sentences and keep everything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from support_assistant.contracts import DraftReply
from support_assistant.guardrails import medical
from support_assistant.guardrails._text import number_words_to_digits, split_sentences

_I = re.IGNORECASE

# ---- domain categories (docs/domain.md "Forbidden claims") -------------------------------------
REFUND_AMOUNT = "unconfirmed_refund_amount"  # 1
REFUND_DATE = "unconfirmed_refund_date"  # 1
DELIVERY_DATE = "unconfirmed_delivery_date"  # 2
MEDICAL = "medical_advice"  # 3
POLICY_EXCEPTION = "policy_exception"  # 4
INTERNAL_LEAK = "internal_leak"  # 5
# grounding categories that support the five above
ELIGIBILITY = "unconfirmed_eligibility"
UNGROUNDED_TERM = "ungrounded_policy_term"
UNCONFIRMED_DATE = "unconfirmed_date"

# ---- sentence contexts -----------------------------------------------------------------------
REFUND_CTX = re.compile(
    r"\b(refund\w*|money\s+back|reimburs\w*|credit(?:ed)?\s+(?:back|to)|"
    r"(?:get|receive)\b[^.]{0,30}?\bback|paid\s+back|repaid|repay\w*)\b",
    _I,
)
DELIVERY_CTX = re.compile(
    r"\b(deliver\w*|arriv\w*|ship(?:s|ped|ping)?|dispatch\w*|reach(?:es)?\s+you|eta|courier|"
    r"out\s+for\s+delivery|in\s+transit|get\s+(?:it|your\s+order))\b",
    _I,
)
COMMERCE_CTX = re.compile(
    r"\b(return\w*|exchange|replace\w*|restocking|fee|charge\w*|window)\b", _I
)
_YOUR_FUTURE = re.compile(
    r"\byour\s+(?:order|package|parcel|device|item|refund|money|\w+-\d+)\b[^.]*?"
    r"\b(?:will|should|is\s+going\s+to|'ll|is\s+expected\s+to)\b|\byou(?:'ll|\s+will|\s+should)\s+"
    r"(?:get|receive|have|see)\b",
    _I,
)
_INSPECTION = re.compile(r"\b(inspect\w*|after\s+(?:it|the\s+item)\s+(?:reaches|arrives))\b", _I)

# ---- extractors ------------------------------------------------------------------------------
_NUM = r"\d[\d,]*(?:\.\d+)?"
_MONEY = re.compile(rf"(?:₹|\brs\.?|\binr\b)\s*({_NUM})|({_NUM})\s*(?:/-|rupees\b|rs\b|inr\b)", _I)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b|per\s+cent\b)", _I)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_DMY = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON},?(?:\s+(\d{{4}}))?\b", _I
)
_DATE_MDY = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", _I)
_DATE_NUM = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})\b")
_RELATIVE = re.compile(
    r"\b(today|tonight|tomorrow|day\s+after\s+tomorrow|(?:by|on|this|next|before|until)\s+"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day|this\s+week(?:end)?|next\s+week|"
    r"end\s+of\s+(?:the|this)\s+week|by\s+(?:the\s+)?weekend|within\s+(?:24|48|72)\s+hours|"
    r"in\s+(?:a|one|two|a\s+few|a\s+couple\s+of)\s+(?:days?|weeks?))\b",
    _I,
)
_DURATION = re.compile(
    r"\b(\d+)(?:\s*(?:-|–|—|to)\s*(\d+))?\s*(business\s+|working\s+)?(hour|day|week|month|year)s?\b",
    _I,
)
_BARE_NUM = re.compile(
    r"(?<![\w.,/-])(\d[\d,]*(?:\.\d+)?)(?![\d,]*\s*(?:%|percent|days?|business|working|hours?|"
    r"weeks?|months?|years?|mmhg|bpm|am|pm|st\b|nd\b|rd\b|th\b|/|-|readings?|kg|cm))",
    _I,
)

_NEGATION = re.compile(
    r"\b(?:not|never|can'?t|cannot|can\s+not|unable|won'?t|isn'?t|aren'?t|don'?t|doesn'?t|"
    r"no\s+way|haven'?t|hasn'?t|nor)\b|n't\b",
    _I,
)

_ELIGIBLE = re.compile(
    r"\b(?:you(?:'re|\s+are)|your\s+(?:return|order|item|device|request|\w+-\d+)(?:\s+is)?|"
    r"it(?:'s|\s+is)|(?:this|the)\s+(?:item|order|return)\s+is)\s+(not\s+|in)?eligible\b|"
    r"\b(?:qualif(?:y|ies))\s+for\s+(?:a\s+)?(?:full\s+)?(?:refund|return)\b",
    _I,
)
_APPROVAL = re.compile(
    r"\b(?:i|we)(?:'ve|\s+have|\s+had)?\s+(?:already\s+|just\s+|now\s+)?(?:approved|issued|"
    r"processed|initiated|authori[sz]ed|refunded|credited|waived|granted)\b|"
    r"\b(?:refund|return|request|exception|fee|amount)\s+(?:has|have|is)\s+(?:already\s+)?"
    r"(?:been\s+)?(?:approved|issued|processed|initiated|authori[sz]ed|credited|waived|granted)\b|"
    r"\b(?:i|we)(?:'ll|\s+will|\s+can|\s+could)\s+(?:waive|approve|make\s+an\s+exception|"
    r"skip\s+the\s+(?:inspection|fee))\b|\bwaiv(?:e|ed|ing)\b|\bmake\s+an\s+exception\b",
    _I,
)
_NO_FEE = re.compile(r"\b(?:no|without\s+(?:the|a|any)?|free\s+of)\s*restocking\s+fee\b", _I)
_LEAK = re.compile(
    r"\b(?:system\s+prompt|my\s+(?:instructions|guidelines|prompt|rules)|i\s+(?:was|am|have\s+"
    r"been)\s+(?:instructed|told|programmed|prompted)|internal\s+(?:notes?|tools?|instructions|"
    r"policy\s+notes?)|confirmed_fields|tool_results?|refund_estimate_inr|get_order_status|"
    r"check_return_eligibility|guardrails?|json\s+schema)\b",
    _I,
)
_MED_EVAL = re.compile(
    r"\b(normal|abnormal|too\s+high|too\s+low|high|low|elevated|fine|healthy|okay|ok|"
    r"concerning|dangerous|worrying|nothing\s+to\s+worry|(?:within|in)\s+(?:the\s+)?(?:normal|"
    r"healthy|safe)\s+range|hypertensi\w*|hypotensi\w*|hypoxi\w*|tachycardi\w*|stage\s+[12]|"
    r"pre-?hypertensi\w*|borderline|good\s+reading|bad\s+reading|mild|severe|serious)\b",
    _I,
)
_MED_ADVICE = re.compile(
    r"\b(you\s+(?:may|might|probably|likely)\s+have|diagnos\w*|sounds\s+like|symptom\s+of|"
    r"sign\s+of|take\s+(?:your\s+|a\s+|an\s+|some\s+)?(?:medicine|medication|tablet|pill|"
    r"paracetamol|aspirin|dose|crocin|dolo)|(?:increase|reduce|stop|skip|adjust|change|double)\s+"
    r"(?:your\s+)?(?:medication|medicine|dose|dosage|tablets)|drink\s+(?:more\s+)?water|"
    r"(?:rest|lie\s+down)\s+(?:and|for)|cut\s+(?:down\s+)?(?:on\s+)?salt)\b",
    _I,
)
_FULL_AMOUNT = re.compile(
    r"\b(full|all|entire|whole|every\s+(?:rupee|paisa)|100\s*%|complete)\b", _I
)
_DEVICE_ACCURACY = re.compile(
    r"\b(cold\s+fingers?|nail\s+polish|movement|moving|talking|cuff\s+(?:fit|size|position\w*|"
    r"wrap\w*)|wrap\w*|posture|batter\w*|accura\w*|calibrat\w*|error\s+code|e[1-5]\b|±)",
    _I,
)


@dataclass
class Violation:
    category: str
    sentence: int  # index into Evidence-checked sentence list, -1 = whole reply
    reason: str
    source: str = "structural"  # structural | rubric | strict


@dataclass
class Evidence:
    confirmed: dict[str, Any] = field(default_factory=dict)
    unconfirmed: dict[str, Any] = field(default_factory=dict)
    passage_text: str = ""

    @classmethod
    def from_draft(cls, draft: DraftReply) -> Evidence:
        ev = cls(
            passage_text=number_words_to_digits("\n".join(p.text for p in draft.passages).lower())
        )
        for tr in draft.tool_results:
            if not tr.ok:
                continue
            for k, v in tr.data.items():
                (ev.confirmed if k in tr.confirmed_fields else ev.unconfirmed)[k] = v
        return ev

    # numbers -------------------------------------------------------------------------------
    @staticmethod
    def _numeric(fields: dict[str, Any], pct: bool) -> dict[float, str]:
        out: dict[float, str] = {}
        for k, v in fields.items():
            if isinstance(v, bool) or not isinstance(v, int | float):
                continue
            if k.endswith(("_pct", "_percent")) == pct:
                out[float(v)] = k
        return out

    @property
    def confirmed_amounts(self) -> dict[float, str]:
        return self._numeric(self.confirmed, pct=False)

    @property
    def unconfirmed_amounts(self) -> dict[float, str]:
        return self._numeric(self.unconfirmed, pct=False)

    @property
    def confirmed_pcts(self) -> set[float]:
        return set(self._numeric(self.confirmed, pct=True))

    @property
    def confirmed_dates(self) -> set[date]:
        out = set()
        for v in self.confirmed.values():
            d = _as_date(v)
            if d:
                out.add(d)
        return out

    @property
    def eligible(self) -> bool | None:
        v = self.confirmed.get("eligible")
        return v if isinstance(v, bool) else None

    def passage_amounts(self) -> set[float]:
        return {_num(a or b) for a, b in _MONEY.findall(self.passage_text)}

    def passage_pcts(self) -> set[float]:
        return {float(x) for x in _PCT.findall(self.passage_text)}

    def passage_durations(self) -> set[tuple[int, int, str]]:
        return {_duration_key(m) for m in _DURATION.finditer(self.passage_text)}

    def summary(self) -> str:
        """Compact evidence for the LLM rubric."""
        conf = ", ".join(f"{k}={v}" for k, v in self.confirmed.items()) or "none"
        unconf = ", ".join(f"{k}={v}" for k, v in self.unconfirmed.items()) or "none"
        return (
            f"CONFIRMED tool facts (may be stated): {conf}\n"
            f"UNCONFIRMED tool values (must NOT be stated as facts or promises): {unconf}\n"
            f"Retrieved policy passages present: {'yes' if self.passage_text else 'no'}"
        )


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _as_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        m = _DATE_ISO.search(v)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
    return None


def _duration_key(m: re.Match[str]) -> tuple[int, int, str]:
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    unit = ("business_" if m.group(3) else "") + m.group(4).lower()
    return lo, hi, unit


def _dates_in(sentence: str) -> list[tuple[int | None, int, int]]:
    """(year|None, month, day) for every absolute date in the sentence."""
    out: list[tuple[int | None, int, int]] = []
    for m in _DATE_ISO.finditer(sentence):
        out.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in _DATE_DMY.finditer(sentence):
        out.append(
            (
                int(m.group(3)) if m.group(3) else None,
                _MONTHS.index(m.group(2).lower()[:3]) + 1,
                int(m.group(1)),
            )
        )
    for m in _DATE_MDY.finditer(sentence):
        if m.group(1).lower() == "may" and not re.match(r"\s*\d", sentence[m.end(1) :]):
            continue
        out.append(
            (
                int(m.group(3)) if m.group(3) else None,
                _MONTHS.index(m.group(1).lower()[:3]) + 1,
                int(m.group(2)),
            )
        )
    for m in _DATE_NUM.finditer(sentence):  # DD/MM/YYYY, Indian convention
        y = int(m.group(3))
        out.append((y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1))))
    return out


def _negated_before(sentence: str, start: int) -> bool:
    return bool(_NEGATION.search(sentence[max(0, start - 40) : start]))


def _mask(sentence: str, patterns: list[re.Pattern[str]]) -> str:
    for p in patterns:
        sentence = p.sub(lambda m: " " * len(m.group(0)), sentence)
    return sentence


@dataclass
class StructuralResult:
    sentences: list[str]
    violations: list[Violation]
    confirmed_facts_in: set[int]  # sentences that state at least one confirmed fact


def check(
    draft: DraftReply, ev: Evidence, user_text: str = "", *, strict: bool = False
) -> StructuralResult:
    """Run every deterministic rule. `strict` adds the outage/heuristic-only rules."""
    sentences = split_sentences(draft.text)
    violations: list[Violation] = []
    confirmed_in: set[int] = set()
    user_scan = medical.scan(user_text)
    # numbers the user reported as readings ("88", "150/95"): a draft sentence repeating one is
    # talking about the user's health even without a keyword like "SpO2"
    reading_nums = {n for r in user_scan.readings for n in re.findall(r"\d+(?:/\d+)?", r)}
    reading_num_re = (
        re.compile(r"(?<![\d/])(?:" + "|".join(map(re.escape, reading_nums)) + r")(?![\d/])")
        if reading_nums
        else None
    )
    p_amounts, p_pcts, p_durs = ev.passage_amounts(), ev.passage_pcts(), ev.passage_durations()

    for i, raw in enumerate(sentences):
        s = number_words_to_digits(raw)
        refund, delivery = bool(REFUND_CTX.search(s)), bool(DELIVERY_CTX.search(s))
        commerce = refund or delivery or bool(COMMERCE_CTX.search(s))

        def flag(cat: str, why: str, i: int = i) -> None:
            violations.append(Violation(cat, i, why))

        # 1. money
        for m in _MONEY.finditer(s):
            val = _num(m.group(1) or m.group(2))
            if val in ev.confirmed_amounts:
                confirmed_in.add(i)
                continue
            src = ev.unconfirmed_amounts.get(val)
            if refund:
                why = f"₹{val:g} is {'the unconfirmed ' + src if src else 'not a confirmed amount'}"
                flag(REFUND_AMOUNT, why)
            elif val not in p_amounts:
                flag(
                    REFUND_AMOUNT if src else UNGROUNDED_TERM,
                    f"₹{val:g} not in confirmed tool fields or retrieved passages",
                )
        if refund:
            masked = _mask(
                s,
                [
                    _MONEY,
                    _DATE_ISO,
                    _DATE_DMY,
                    _DATE_MDY,
                    _DATE_NUM,
                    _DURATION,
                    re.compile(r"PN-\d+", _I),
                ],
            )
            for m in _BARE_NUM.finditer(masked):
                val = _num(m.group(1))
                if val >= 100 and val not in ev.confirmed_amounts:
                    src = ev.unconfirmed_amounts.get(val)
                    flag(
                        REFUND_AMOUNT,
                        f"bare amount {val:g} in refund context"
                        + (f" equals unconfirmed {src}" if src else ""),
                    )

        # percentages (restocking fee)
        if commerce:
            for m in _PCT.finditer(s):
                val = float(m.group(1))
                if val in ev.confirmed_pcts:
                    confirmed_in.add(i)
                elif val not in p_pcts:
                    flag(UNGROUNDED_TERM, f"{val:g}% not confirmed or in retrieved policy")

        # 1/2. absolute dates
        for y, mo, d in _dates_in(s):
            if any(
                c.month == mo and c.day == d and (y is None or c.year == y)
                for c in ev.confirmed_dates
            ):
                confirmed_in.add(i)
                continue
            cat = DELIVERY_DATE if delivery else REFUND_DATE if refund else UNCONFIRMED_DATE
            flag(cat, f"date {d:02d}/{mo:02d} not in a confirmed tool field")

        # 1/2. relative day promises
        if refund or delivery:
            for m in _RELATIVE.finditer(s):
                if m.group(0).lower() in ev.passage_text:
                    continue
                flag(
                    DELIVERY_DATE if delivery else REFUND_DATE,
                    f"relative date '{m.group(0)}' is not confirmed",
                )

        # durations: policy terms must be retrieved; order-specific promises need more
        if commerce:
            for m in _DURATION.finditer(s):
                key = _duration_key(m)
                if key not in p_durs:
                    cat = DELIVERY_DATE if delivery else REFUND_DATE if refund else UNGROUNDED_TERM
                    flag(cat, f"'{m.group(0)}' not in retrieved policy passages")
                elif delivery and _YOUR_FUTURE.search(s) and not ev.confirmed_dates:
                    flag(DELIVERY_DATE, f"policy range '{m.group(0)}' stated as this order's ETA")
                elif refund and not _INSPECTION.search(s):
                    flag(
                        REFUND_DATE,
                        f"refund timeline '{m.group(0)}' stated without the inspection condition",
                    )

        # eligibility
        for m in _ELIGIBLE.finditer(s):
            neg = bool(
                m.group(1) and m.group(1).strip().lower() in ("not", "in")
            ) or _negated_before(s, m.start())
            if ev.eligible is None:
                flag(ELIGIBILITY, "eligibility stated without a confirmed eligibility check")
            elif ev.eligible == neg:
                flag(ELIGIBILITY, f"draft says eligible={not neg}, tool confirmed {ev.eligible}")
            else:
                confirmed_in.add(i)

        # 4. approvals, waivers, exceptions
        for m in _APPROVAL.finditer(s):
            if not _negated_before(s, m.start()) and not _NEGATION.search(m.group(0)):
                flag(POLICY_EXCEPTION, f"claims an approval/waiver: '{m.group(0)}'")
                break
        for m in _NO_FEE.finditer(s):
            if ev.confirmed.get("restocking_fee_pct") == 0:
                confirmed_in.add(i)
            elif not _negated_before(s, m.start()):
                flag(POLICY_EXCEPTION, "says no restocking fee without a confirmed 0% fee")

        # 5. internal leaks (a refusal that names the thing is fine)
        for m in _LEAK.finditer(s):
            if not _negated_before(s, m.start()):
                flag(INTERNAL_LEAK, f"mentions internal detail '{m.group(0)}'")
                break

        # 3. medical
        echoes_reading = bool(reading_num_re and reading_num_re.search(s))
        anchored = bool(medical.READING_ANCHOR.search(s)) or echoes_reading
        troubleshooting = bool(_DEVICE_ACCURACY.search(s))
        if _MED_ADVICE.search(s):
            flag(MEDICAL, "diagnosis or treatment advice")
        elif anchored and _MED_EVAL.search(s) and not troubleshooting:
            flag(MEDICAL, "interprets a health reading")
        elif (
            anchored
            and not troubleshooting
            and (
                user_scan.emergency  # only the fixed safety message may talk about health
                or (strict and echoes_reading)
            )
        ):
            flag(MEDICAL, "health statement while the safety rule restricts them")

        # 1. paraphrased whole-amount promises ("you'll get all your money back")
        if refund and _YOUR_FUTURE.search(s) and _FULL_AMOUNT.search(s):
            flag(REFUND_AMOUNT, "promises the full amount before inspection")
        elif strict and refund and _YOUR_FUTURE.search(s) and not _INSPECTION.search(s):
            violations.append(
                Violation(
                    REFUND_AMOUNT,
                    i,
                    "unconditional refund promise (strict mode: rubric unavailable)",
                    "strict",
                )
            )

    return StructuralResult(sentences, violations, confirmed_in)
