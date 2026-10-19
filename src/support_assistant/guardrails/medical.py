"""Domain safety rule (docs/domain.md): red-flag readings/symptoms and the fixed safety message.

Shared by both guards. The input guard tags red-flag messages (never blocks them: the user needs
help); the output guard re-derives the same flags from the user text and makes sure the fixed
safety message is the only health statement in the reply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SAFETY_MESSAGE = (
    "Please seek medical care now or call 112: the reading or symptoms you describe "
    "need urgent attention from a medical professional."
)
MEDICAL_DEFLECTION = (
    "I can't interpret health readings or give medical advice, so please check this reading "
    "with your doctor. What I can do is help make sure your device is measuring accurately "
    "(fit, positioning, batteries and error codes)."
)

# Red-flag thresholds from docs/domain.md
SPO2_MAX_SAFE = 92  # SpO2 < 92 %
BP_SYS_FLAG, BP_DIA_FLAG = 180, 120  # >= 180 systolic OR >= 120 diastolic
TEMP_C_FLAG = 39.5

_SPO2 = re.compile(
    r"\b(?:spo2|sp\s?o2|o2|oxygen(?:\s+(?:saturation|level|levels|sat))?|saturation|sats?|"
    r"ox-?02|oxi\s?o2|oximeter)\b"
    r"\D{0,25}?(\d{2,3})(?:\.\d+)?\s*%?"
    r"|(\d{2,3})\s*%[\s,:;-]*(?:spo2|sp\s?o2|o2|oxygen|saturation)",
    re.IGNORECASE,
)
_BP = re.compile(r"(?<![\d/.-])(\d{2,3})\s*/\s*(\d{2,3})(?![\d/.-])")
_BP_CONTEXT = re.compile(
    r"\b(bp|blood\s*pressure|pressure|mmhg|cuff|bp-200|systolic|diastolic|reading)\b", re.I
)
_TEMP = re.compile(
    r"(\d{2,3}(?:\.\d+)?)\s*(?:°|º|deg(?:rees?)?)?\s*(c|f|celsius|fahrenheit)\b"
    r"|\b(?:temp(?:erature)?|fever|th-01)\b\D{0,20}?(\d{2,3}(?:\.\d+)?)",
    re.IGNORECASE,
)
_SYMPTOMS = re.compile(
    r"\b(chest\s+(?:pain|tightness|pressure)|breathless\w*|short(?:ness)?\s+of\s+breath|"
    r"can'?t\s+breathe|cannot\s+breathe|(?:difficulty|trouble|struggling)\s+(?:to\s+)?breath\w*|"
    r"faint(?:ed|ing)|passed\s+out|unconscious|blue\s+lips|slurred\s+speech)\b",
    re.IGNORECASE,
)
READING_ANCHOR = re.compile(
    r"\b(spo2|sp\s?o2|oxygen|saturation|blood\s*pressure|bp|pulse|heart\s*rate|bpm|mmhg|"
    r"systolic|diastolic|temperature|fever|reading|readings|body\s*fat|bmi)\b|\d{2,3}\s*/\s*\d{2,3}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MedicalScan:
    red_flags: tuple[str, ...]
    readings: tuple[str, ...]  # literal reading strings the user gave, e.g. "150/95", "88%"

    @property
    def emergency(self) -> bool:
        return bool(self.red_flags)


def scan(text: str) -> MedicalScan:
    flags: list[str] = []
    readings: list[str] = []
    for m in _SPO2.finditer(text):
        val = int(m.group(1) or m.group(2))
        if not 50 <= val <= 100:  # "OX-02 arrived 10 days ago" is not a reading
            continue
        readings.append(m.group(0).strip())
        if val < SPO2_MAX_SAFE:
            flags.append(f"spo2<{SPO2_MAX_SAFE}")
    if _BP_CONTEXT.search(text):
        for m in _BP.finditer(text):
            sys_, dia = int(m.group(1)), int(m.group(2))
            if 60 <= sys_ <= 300 and 30 <= dia <= 200 and sys_ > dia:
                readings.append(m.group(0))
                if sys_ >= BP_SYS_FLAG or dia >= BP_DIA_FLAG:
                    flags.append("bp>=180/120")
    for m in _TEMP.finditer(text):
        val = float(m.group(1) or m.group(3))
        unit = (m.group(2) or "").lower()[:1]
        if not unit:
            unit = "c" if 30 <= val <= 45 else "f" if 90 <= val <= 115 else ""
        celsius = val if unit == "c" else (val - 32) * 5 / 9 if unit == "f" else None
        if celsius is None or not 30 <= celsius <= 45:
            continue
        readings.append(m.group(0).strip())
        if celsius >= TEMP_C_FLAG:
            flags.append("temp>=39.5C")
    for m in _SYMPTOMS.finditer(text):
        flags.append(f"symptom:{m.group(1).lower()}")
    return MedicalScan(red_flags=tuple(dict.fromkeys(flags)), readings=tuple(readings))
