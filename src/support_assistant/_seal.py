"""Capability token for minting CheckedMessage.

Only support_assistant.contracts and support_assistant.guardrails may import this module.
That rule is enforced by the import-linter contract in pyproject.toml, so "an unchecked
message cannot reach a handler" is checked by tooling rather than assumed.
"""

SEAL = object()
