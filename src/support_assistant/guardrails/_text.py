"""Text normalization and decoded "views" of untrusted input.

The input guard never matches patterns against the raw string. It builds several views of the
same message (normalized, leetspeak-folded, de-spaced, and anything that decodes from base64,
rot13, URL-encoding or Unicode tag characters) and runs the same signals over every view. A
signal that only fires in a decoded view is itself evidence of obfuscation: customers who want
a refund do not base64-encode the request.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote

# Cyrillic/Greek letters that render like Latin ones. NFKC does not fold these.
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i",
        "ј": "j", "ѕ": "s", "ԁ": "d", "ӏ": "l", "к": "k", "м": "m", "т": "t", "в": "b",
        "н": "h", "г": "r", "ц": "u", "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
        "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X", "І": "I", "Ј": "J",
        "Ѕ": "S", "ο": "o", "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ρ": "p",
        "τ": "t", "υ": "u", "χ": "x", "Ο": "O", "Α": "A", "Ε": "E", "Ι": "I", "Κ": "K",
        "Ν": "N", "Ρ": "P", "Τ": "T", "Χ": "X", "Β": "B", "Μ": "M", "Ζ": "Z", "Η": "H",
    }
)  # fmt: skip
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a",
                       "$": "s", "!": "i", "|": "l"})  # fmt: skip

_TAG_LO, _TAG_HI = 0xE0000, 0xE007F  # Unicode "tag" block, used for invisible ASCII smuggling
_B64_TOKEN = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
_LEET_TOKEN = re.compile(r"\S*[a-z]\S*")
_SPACED = re.compile(r"\b(?:[a-z][ .\-_*·]){3,}[a-z]\b")
_WS = re.compile(r"\s+")
_DELIM_LIKE = re.compile(r"</?\s*(?:data|untrusted)[-_][^>]{0,40}>", re.IGNORECASE)

_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10", "fifteen": "15", "thirty": "30",
}  # fmt: skip
_NUMBER_WORD_RE = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b")


@dataclass(frozen=True)
class TextViews:
    """All the ways one message can be read. `views[name]` is lowercase normalized text."""

    normalized: str  # NFKC + invisible-char strip + homoglyph fold, original case
    views: dict[str, str]
    obfuscation: tuple[str, ...] = field(default=())  # e.g. ("zero_width", "homoglyph")

    @property
    def decoded_views(self) -> dict[str, str]:
        """Views that exist only because something was encoded."""
        return {
            k: v for k, v in self.views.items() if k.startswith(("base64", "rot13", "url", "tag"))
        }


def normalize(text: str) -> tuple[str, list[str]]:
    """NFKC, strip invisible/format characters, fold homoglyphs. Returns (text, findings)."""
    findings: list[str] = []
    out = unicodedata.normalize("NFKC", text)
    chars = []
    for ch in out:
        cp = ord(ch)
        if _TAG_LO <= cp <= _TAG_HI:
            if "unicode_tags" not in findings:
                findings.append("unicode_tags")
            continue
        if unicodedata.category(ch) == "Cf":  # zero-width space/joiner, BOM, bidi controls
            if "zero_width" not in findings:
                findings.append("zero_width")
            continue
        chars.append(ch)
    out = "".join(chars)
    folded = out.translate(_HOMOGLYPHS)
    if folded != out and _has_mixed_script_word(out):
        findings.append("homoglyph")
    return folded, findings


def _has_mixed_script_word(text: str) -> bool:
    for word in text.split():
        scripts = set()
        for ch in word:
            if ch.isalpha():
                name = unicodedata.name(ch, "")
                scripts.add(name.split(" ")[0])
        if "LATIN" in scripts and scripts & {"CYRILLIC", "GREEK"}:
            return True
    return False


def _tag_payload(text: str) -> str:
    return "".join(
        chr(ord(ch) - _TAG_LO) for ch in text if _TAG_LO + 0x20 <= ord(ch) <= _TAG_LO + 0x7E
    )


def _printable_ratio(s: str) -> float:
    if not s:
        return 0.0
    return sum(ch.isprintable() or ch in "\n\t" for ch in s) / len(s)


def _decode_base64_spans(text: str) -> list[str]:
    decoded = []
    for tok in _B64_TOKEN.findall(text):
        if tok.isalpha() or tok.isdigit():  # long plain word or number, not an encoding
            continue
        padded = tok + "=" * (-len(tok) % 4)
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw = decoder(padded)
                s = raw.decode("utf-8")
            except (binascii.Error, ValueError, UnicodeDecodeError):
                continue
            if _printable_ratio(s) > 0.9 and re.search(r"[a-zA-Z]{3,}", s):
                decoded.append(s)
                break
    return decoded


def _lower(text: str) -> str:
    return _WS.sub(" ", text.lower()).strip()


_ONE_AS_L = re.compile(r"(?<=[aeiou])1")


def leet_fold(text: str) -> str:
    """Fold leetspeak only inside tokens that already contain letters ('1gn0re' -> 'ignore').
    '1' is ambiguous: after a vowel it is usually 'l' ('ru1es'), otherwise 'i' ('1gn0re')."""

    def fold(tok: str) -> str:
        return _ONE_AS_L.sub("l", tok).translate(_LEET)

    return _LEET_TOKEN.sub(lambda m: fold(m.group(0)), text)


def build_views(text: str) -> TextViews:
    normalized, findings = normalize(text)
    base = _lower(normalized)
    views: dict[str, str] = {"plain": base}

    leet = leet_fold(base)
    if leet != base:
        views["leet"] = leet
    # De-space before whitespace is collapsed: "i g n o r e  y o u r" keeps its word gaps.
    chunks = re.split(r"\s{2,}", normalized.lower())
    despaced = _lower(
        " ".join(_SPACED.sub(lambda m: re.sub(r"[ .\-_*·]", "", m.group(0)), c) for c in chunks)
    )
    if despaced != base:
        views["despaced"] = despaced
        findings.append("spaced_letters")

    compact = re.sub(r"[^a-z]", "", leet_fold(base))
    views["compact"] = compact

    tag = _tag_payload(unicodedata.normalize("NFKC", text))
    if tag.strip():
        views["tag"] = _lower(tag)
    for i, payload in enumerate(_decode_base64_spans(normalized)):
        views[f"base64_{i}"] = _lower(normalize(payload)[0])
    if "%" in base:
        url = _lower(unquote(base))
        if url != base:
            views["url"] = url
    views["rot13"] = _lower(codecs.decode(base, "rot13"))

    return TextViews(normalized=normalized, views=views, obfuscation=tuple(findings))


def number_words_to_digits(text: str) -> str:
    """'three business days' -> '3 business days', so paraphrased durations still match."""
    return _NUMBER_WORD_RE.sub(lambda m: _NUMBER_WORDS[m.group(1)], text)


def neutralize_delimiters(text: str) -> str:
    """Defang anything that looks like our data delimiters (the nonce makes them unguessable
    anyway; this just keeps fake closing tags from confusing a small model)."""
    return _DELIM_LIKE.sub("[tag removed]", text)


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9₹\"'(])|\n+")
_ABBREV = re.compile(r"\b(Rs|approx|e\.g|i\.e|etc|No|vs)\.", re.IGNORECASE)


def split_sentences(text: str) -> list[str]:
    """Sentence split that survives 'Rs. 500' and bullet lists. Returns non-empty stripped parts."""
    protected = _ABBREV.sub(lambda m: m.group(0)[:-1] + "\x00", text)
    parts = [p.replace("\x00", ".").strip() for p in _SENT_SPLIT.split(protected)]
    return [p for p in parts if p]
