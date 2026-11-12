from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from backend.contracts.schemas.core import Contract

MemoryRecordStatus = Literal["available", "superseded", "unavailable"]
MemoryRetention = Literal["run", "session", "task"]
TaskStepStatus = Literal["pending", "in_progress", "completed", "blocked"]


class MemoryLink(Contract):
    relation: str = Field(min_length=1)
    target_id: str = Field(min_length=1)


class MemoryRecord(Contract):
    """Canonical stored event; ``body`` stays open for business-specific data."""

    record_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    run_id: str | None = Field(default=None, min_length=1)
    task_id: str | None = Field(default=None, min_length=1)
    kind: str = Field(default="interaction", min_length=1)
    source: str = Field(min_length=1)
    summary: str = ""
    body: dict[str, Any] = Field(default_factory=dict)
    updates: dict[str, Any] = Field(default_factory=dict)
    links: list[MemoryLink] = Field(default_factory=list)
    status: MemoryRecordStatus = "available"
    retention: MemoryRetention = "run"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_feedback(cls, value):
        # Old FeedbackRecord objects share these fields; avoid a circular import.
        if isinstance(value, Contract) and not isinstance(value, cls):
            return value.model_dump()
        return value

    @property
    def payload(self) -> dict[str, Any]:
        return self.body or self.updates

    @property
    def exchange_ids(self) -> list[str]:
        """Protocol correlation keys, scoped by session and run in repositories."""
        if self.source == "reasoning":
            proposal = self.payload.get("proposal", {})
            if isinstance(proposal, dict) and proposal.get("kind") == "action":
                requests = proposal.get("action_requests", [])
                if not isinstance(requests, list):
                    return []
                return [
                    str(c["action_id"])
                    for c in requests
                    if isinstance(c, dict) and c.get("action_id")
                ]
        if self.source == "action":
            action = self.payload.get("action", {})
            if isinstance(action, dict) and action.get("action_id"):
                return [str(action["action_id"])]
        return []


class TaskStep(Contract):
    step_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: TaskStepStatus = "pending"
    evidence_refs: list[str] = Field(default_factory=list)


class TaskState(Contract):
    task_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    goal: str = ""
    steps: list[TaskStep] = Field(default_factory=list)
    current_step_id: str | None = None
    blockers: list[str] = Field(default_factory=list)
    revision: int = Field(default=0, ge=0)
    updated_by: str = Field(default="harness", min_length=1)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MemoryWriteResult(Contract):
    accepted_record_ids: list[str] = Field(default_factory=list)
    duplicate_record_ids: list[str] = Field(default_factory=list)
    conflict_record_ids: list[str] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)


class TaskWriteResult(Contract):
    accepted: bool
    conflict: bool = False
    task_state: TaskState | None = None
    notices: list[str] = Field(default_factory=list)


class MemoryRecall(Contract):
    """A bounded canonical view; items is the legacy compatibility projection."""

    # ``scope`` and ``items`` preserve the first vertical slice contract.
    scope: str = Field(min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    run_id: str | None = Field(default=None, min_length=1)
    task_id: str | None = None
    task_state: TaskState | None = None
    items: list[MemoryRecord] = Field(default_factory=list)
    records: list[MemoryRecord] = Field(default_factory=list)
    selected_item_ids: list[str] = Field(default_factory=list)
    omitted_item_ids: list[str] = Field(default_factory=list)
    budget_chars: int = Field(default=0, ge=0)
    used_chars: int = Field(default=0, ge=0)
    selection_policy: str = "deterministic_layers"
    notices: list[str] = Field(default_factory=list)
    required_item_ids: list[str] = Field(default_factory=list)
    omitted_reasons: dict[str, str] = Field(default_factory=dict)
    blocked_reason: str | None = None
    trace: dict[str, Any] = Field(default_factory=dict)
    write: MemoryWriteResult | None = None
    task_write: TaskWriteResult | None = None
    closed_run: bool = False


class MemoryInput(Contract):
    operation: Literal["recall", "record", "restore", "update_task", "close_run", "clear"]
    session_id: str | None = Field(default=None, min_length=1)
    task_id: str | None = Field(default=None, min_length=1)
    items: list[MemoryRecord] = Field(default_factory=list)
    task_state: TaskState | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    reference_ids: list[str] = Field(default_factory=list)
    max_items: int | None = Field(default=None, ge=1)
    max_chars: int | None = Field(default=None, ge=1)
    purge_session: bool = False


MemoryOutput = MemoryRecall
