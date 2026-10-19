"""Intent -> Handler registry. The router's prompt and JSON-schema enum are built from it, so
adding an agent is: write a Handler, register it, add eval rows. No router edits."""

from __future__ import annotations

from collections.abc import Iterable

from support_assistant.contracts import Handler, Intent


class DuplicateIntentError(ValueError):
    pass


class HandlerRegistry:
    def __init__(self, handlers: Iterable[Handler] = ()) -> None:
        self._by_intent: dict[Intent, Handler] = {}
        for h in handlers:
            self.register(h)

    def register(self, handler: Handler) -> None:
        """Register `handler` for every intent it declares. All-or-nothing on conflict.

        A handler serving several intents may define `intent_descriptions: Mapping[Intent, str]`
        so the router can tell them apart; otherwise its `description` is used for each.
        """
        if not handler.intents:
            raise ValueError(f"handler {handler.name!r} declares no intents")
        taken = [i for i in handler.intents if i in self._by_intent]
        if taken:
            owners = {i.value: self._by_intent[i].name for i in taken}
            raise DuplicateIntentError(f"{handler.name!r}: intents already registered: {owners}")
        for intent in handler.intents:
            self._by_intent[intent] = handler

    def get(self, intent: Intent) -> Handler | None:
        return self._by_intent.get(intent)

    def __contains__(self, intent: object) -> bool:
        return intent in self._by_intent

    @property
    def intents(self) -> tuple[Intent, ...]:
        return tuple(self._by_intent)

    def descriptions(self) -> dict[Intent, str]:
        """Intent -> one-line description for the router prompt, in registration order."""
        out: dict[Intent, str] = {}
        for intent, handler in self._by_intent.items():
            out[intent] = handler.intent_descriptions.get(intent, handler.description)
        return out
