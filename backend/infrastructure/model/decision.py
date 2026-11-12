"""Incremental projection of a delivery control into ordinary visible text."""

from pydantic_core import from_json


class DeliveryText:
    def __init__(self):
        self.emitted = ""

    def push(self, arguments):
        value = from_json(arguments, allow_partial="trailing-strings")
        text = value.get("text", "") if isinstance(value, dict) else ""
        if not isinstance(text, str) or not text.startswith(self.emitted):
            raise ValueError("non_monotonic_delivery_text")
        delta, self.emitted = text[len(self.emitted) :], text
        return delta
