from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from backend.contracts.model_context import RequestBudget
from backend.contracts.schemas.core import Contract

ContextItemType = Literal["message", "tool_call", "tool_result", "resource"]
ContextRole = Literal["system", "developer", "user", "assistant", "tool"]
ContextStatus = Literal[
    "available", "partial", "pending", "failed", "unavailable", "superseded"
]


class ContextLink(Contract):
    """An explicit relationship between two context items."""

    relation: str = Field(min_length=1)
    target_id: str = Field(min_length=1)


class ContextItem(Contract):
    """A typed, attributed unit that perception may place in the model context.

    ``body`` stays open because maritime facts, evidence, media and tool payloads
    do not share one business schema. The stable envelope is what the harness
    relies on for identity, ordering and safe assembly.
    """

    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    type: ContextItemType
    source: str = Field(min_length=1)
    kind: str = Field(default="generic", min_length=1)
    role: ContextRole | None = None
    status: ContextStatus = "available"
    body: dict[str, Any] = Field(default_factory=dict)
    links: list[ContextLink] = Field(default_factory=list)
    origin_run_id: str | None = None
    # Public-text projection only; absent from provider encoding and trace dumps.
    identity_labels: dict[str, str] = Field(default_factory=dict, exclude=True)


class ContextAssembly(Contract):
    """Non-model metadata explaining the current context view."""

    budget: RequestBudget | None = None
    used_tokens: int = Field(default=0, ge=0)
    selection_policy: str = "current_then_recent_complete_units"
    selected_item_ids: list[str] = Field(default_factory=list)
    omitted_item_ids: list[str] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    required_item_ids: list[str] = Field(default_factory=list)
    omitted_reasons: dict[str, str] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)
