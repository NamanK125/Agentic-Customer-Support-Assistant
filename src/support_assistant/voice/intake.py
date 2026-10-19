"""Call intake: ask for the caller's details up front, so later turns never stall on them.

On connect the assistant asks for name and order number together with the request. Every
utterance is scanned deterministically (no LLM) for those details, which are kept in a per-call
`CallerProfile` and passed to the text pipeline as structured context. Then a later "where is my
order?" goes straight to the tool, instead of costing a full extra turn (STT + LLM + TTS) on
"which order?". An utterance that only gives details gets an instant templated acknowledgement,
and only details that are still missing are asked for, once each.

The profile is format-constrained (`CallerProfile`), and the name is never put into an LLM
prompt; it is used only for the spoken acknowledgement and the human handoff ticket.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from support_assistant.contracts import CallerProfile, FinalReply
from support_assistant.observability import meter
from support_assistant.voice.contracts import VoiceReply

_fast_acks = meter(__name__).create_counter("voice.intake.fast_ack")

GREETING = (
    "Thanks for calling PulseNest support. To help you faster, please tell me your name and your "
    "order number if you have one, and what you need help with."
)

Handle = Callable[[str, str, CallerProfile | None], Awaitable[FinalReply]]

_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "to": "2", "too": "2", "three": "3",
    "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}  # fmt: skip
_REPEAT = {"double": 2, "triple": 3}
_TOKEN = re.compile(r"[A-Za-z]+|\d+")
# Words that can follow "I'm"/"this is" without being a name.
_NOT_NAMES = frozenset(
    "a an the calling looking trying having not just very so really here sorry ok okay fine good "
    "wondering checking asking interested unable able going waiting still also from with about "
    "your my customer".split()
)
_NAME = re.compile(
    r"\b(?:my name is|my name's|name is|this is|i am|i'm|it's|call me)\s+"
    r"([A-Za-z][A-Za-z'-]{1,20}(?:\s+[A-Za-z][A-Za-z'-]{1,20})?)",
    re.IGNORECASE,
)
# Words that don't count as a request when deciding if an utterance only gave details.
_FILLER = frozenset(
    "hi hello hey yes yeah yep sure ok okay so um uh er and my name is name's this it's i'm i am "
    "me call order number id the a an of it its is pn p n thanks thank you please here that's "
    "that was i m s re ve ll d".split()  # contraction pieces: the tokenizer splits "I'm" into i, m
)


def find_spoken_order_id(text: str) -> str | None:
    """`PN-10002` from typed or spoken forms: "PN-10002", "P N 10002", "p.n. one zero zero zero
    two", "PN one double zero zero two", "order number 10002" (a bare 5-digit number counts only
    next to the word "order", and must look like our ids: 10xxx)."""
    tokens = [t.lower() for t in _TOKEN.findall(text)]
    i = 0
    while i < len(tokens):
        t = tokens[i]
        prefixed = False
        j = i
        if t == "pn":
            prefixed, j = True, i + 1
        elif t == "p" and i + 1 < len(tokens) and tokens[i + 1] == "n":
            prefixed, j = True, i + 2
        elif t == "order" or (t == "number" and i > 0 and tokens[i - 1] == "order"):
            j = i + 1
            while j < len(tokens) and tokens[j] in ("number", "id", "is", "it's", "its"):
                j += 1
        else:
            i += 1
            continue
        digits = _read_digits(tokens, j)
        if len(digits) == 5 and (prefixed or digits.startswith("10")):
            return f"PN-{digits}"
        i += 1
    return None


def _read_digits(tokens: list[str], j: int) -> str:
    out = ""
    while j < len(tokens) and len(out) < 6:
        t = tokens[j]
        if t.isdigit():
            out += t
        elif t in _DIGIT_WORDS:
            out += _DIGIT_WORDS[t]
        elif t in _REPEAT and j + 1 < len(tokens) and tokens[j + 1] in _DIGIT_WORDS:
            out += _DIGIT_WORDS[tokens[j + 1]] * _REPEAT[t]
            j += 1
        else:
            break
        j += 1
    return out


def find_name(text: str) -> str | None:
    for m in _NAME.finditer(text):
        words = m.group(1).split()
        if words[0].lower() in _NOT_NAMES:
            continue
        # Keep the second word only if it also looks like part of a name.
        if len(words) > 1 and (words[1].lower() in _NOT_NAMES or words[1].lower() in _FILLER):
            words = words[:1]
        name = " ".join(w.capitalize() for w in words)
        try:
            return CallerProfile(name=name).name
        except ValueError:
            return None
    return None


def only_details(text: str, profile_name: str | None, order_id: str | None) -> bool:
    """True if the utterance carries no request beyond name/order details (e.g. "Hi, I'm Asha,
    order P N one zero zero zero two")."""
    if not (profile_name or order_id) or "?" in text:
        return False
    words = [w.lower() for w in _TOKEN.findall(text)]
    name_words = {w.lower() for w in (profile_name or "").split()}
    rest = [
        w for w in words
        if w not in _FILLER and w not in name_words and not w.isdigit() and w not in _DIGIT_WORDS
        and w not in _REPEAT
    ]  # fmt: skip
    # Strict: any leftover word means there's a request in it. A wrong "details only" costs the
    # caller a turn; a wrong "request" just goes to the pipeline with the details attached.
    return not rest


@dataclass
class _CallState:
    profile: CallerProfile
    asked_name: bool = False
    asked_order: bool = False


class CallIntake:
    """Voice responder: `await intake(session_id, text) -> VoiceReply`, plus `greeting()`.

    `handle` is the text pipeline (`SupportPipeline.handle` with channel="voice").
    """

    def __init__(self, handle: Handle) -> None:
        self._handle = handle
        self._calls: dict[str, _CallState] = {}

    async def greeting(self, session_id: str) -> str:
        self._calls.setdefault(session_id, _CallState(CallerProfile()))
        return GREETING

    def profile(self, session_id: str) -> CallerProfile:
        return self._state(session_id).profile

    def end(self, session_id: str) -> None:
        self._calls.pop(session_id, None)

    def _state(self, session_id: str) -> _CallState:
        return self._calls.setdefault(session_id, _CallState(CallerProfile()))

    async def __call__(self, session_id: str, text: str) -> VoiceReply:
        st = self._state(session_id)
        name, order_id = find_name(text), find_spoken_order_id(text)
        st.profile = CallerProfile(
            name=name or st.profile.name, order_id=order_id or st.profile.order_id
        )

        if only_details(text, name, order_id):
            _fast_acks.add(1)
            return VoiceReply(text=self._ack(st), intent="intake")

        reply = await self._handle(session_id, text, st.profile)
        return VoiceReply(
            text=reply.text,
            blocked=reply.blocked,
            rewritten=reply.rewritten,
            intent=reply.intent.value if reply.intent else None,
            trace_id=reply.trace_id,
        )

    def _ack(self, st: _CallState) -> str:
        p = st.profile
        parts = [f"Thanks, {p.name}." if p.name else "Thanks."]
        if p.order_id:
            parts.append(f"I have order {p.order_id}.")
        if not p.name and not st.asked_name:
            st.asked_name = True
            parts.append("And may I have your name?")
        elif not p.order_id and not st.asked_order:
            st.asked_order = True
            parts.append("If your question is about an order, what's the order number?")
        parts.append("How can I help you today?")
        return " ".join(parts)
