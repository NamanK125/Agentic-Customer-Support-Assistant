"""Input and output guardrails. This package is the only src package allowed to import the
CheckedMessage seal (see pyproject [tool.importlinter] and docs/guardrail_trace.md)."""

from support_assistant.guardrails.input_guard import InputGuardrail
from support_assistant.guardrails.medical import MEDICAL_DEFLECTION, SAFETY_MESSAGE
from support_assistant.guardrails.output_guard import OutputGuardrail

__all__ = ["MEDICAL_DEFLECTION", "SAFETY_MESSAGE", "InputGuardrail", "OutputGuardrail"]
