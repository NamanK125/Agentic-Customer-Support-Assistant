"""Greetings, thanks, goodbyes and "ok, stop" that carry no request.

They get a fixed reply instead of a route. Without this, "Hello." has no intent to match, the
router falls back to escalate_to_human, and a greeting files a human-support ticket. Only a
message made entirely of these words matches: "Hi, where is my order?" still routes normally.
Runs after the input guard, so it is never a way around it.
"""

from __future__ import annotations

import re
from typing import Literal

Kind = Literal["stop", "greeting", "thanks", "bye"]

_GREETING = (
    r"hi+|hello+|hey+|hiya|namaste|good (?:morning|afternoon|evening)|"
    r"anyone there|is anyone there|are you there"
)
_THANKS = r"thanks?|thank you(?: so much| very much)?|thx|ty|great|cool|perfect|got it"
_BYE = r"bye|goodbye|good bye|see you|that'?s all|that is all|nothing else|no thanks"
# "Ok, stop" / "wait" during a reply: the caller wants the talking to end, not a new answer
_STOP = (
    r"stop(?: it| talking| there)?|wait|hold on|enough|that'?s enough|never ?mind|"
    r"cancel(?: that)?"
)
# Allowed around the key words, never enough on their own: a bare "ok", "yes" or "no" is usually
# an answer to the assistant's question and must be routed with that context.
_FILLER = r"ok(?:ay)?|alright|all right|so|um+|uh+|yes|yeah|there|again|please"

_PATTERNS: list[tuple[Kind, re.Pattern[str]]] = [
    (
        kind,
        re.compile(rf"^(?=.*\b(?:{words})\b)(?:(?:{words}|{_FILLER})[\s,.!?]*)+$", re.IGNORECASE),
    )
    for kind, words in (
        ("stop", _STOP),
        ("bye", _BYE),
        ("thanks", _THANKS),
        ("greeting", _GREETING),
    )
]

REPLIES: dict[Kind, str] = {
    "stop": "Alright. Is there anything else I can help you with?",
    "greeting": (
        "Hi, this is PulseNest support. I can check an order, help with a return, or answer "
        "questions about your device. What can I help you with?"
    ),
    "thanks": "You're welcome. Is there anything else I can help you with?",
    "bye": "Thanks for contacting PulseNest. Take care!",
}


def classify(text: str) -> Kind | None:
    t = text.strip()
    if not t or len(t) > 60:
        return None
    for kind, pattern in _PATTERNS:
        if pattern.match(t):
            return kind
    return None
