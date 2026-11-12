"""User-visible run stream events.

These events deliberately contain only lifecycle progress and visible answer
text. Provider reasoning, prompts, credentials and tool arguments stay behind
the Harness boundary.
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from backend.contracts.harness.lifecycle import RunEnvelope
from backend.contracts.schemas.core import Contract, Error

StreamEventType = Literal["progress", "delta", "answer", "done", "error"]


class StreamEvent(Contract):
    run_id: str
    sequence: int = 0
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: StreamEventType
    stage: str | None = None
    label: str | None = None
    text: str | None = None
    # ``done`` only marks the end of visible text.  Lifecycle or a snapshot
    # remains authoritative for the Run terminal state.
    run: RunEnvelope | None = None
    error: Error | None = None


def visible_text(events):
    """Replay answer revisions rather than concatenate every model's preamble."""
    text = ""
    for event in events:
        if event.type == "delta":
            text += event.text or ""
        elif event.type in {"answer", "done"}:
            text = event.text or ""
    return text
