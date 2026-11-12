"""Stable contracts for message dispatch and run lifecycle.

These contracts describe application control state. They deliberately do not
contain maritime business fields or model-provider details.
"""

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from backend.contracts.schemas.core import Contract, Error

SessionStatus = Literal["open", "closed"]
RunStatus = Literal[
    "queued",
    "running",
    "waiting_user",
    "interrupted",
    "stopped",
    "completed",
    "failed",
]
RecoveryMode = Literal["none", "checkpoint", "fresh_attempt"]
CommandKind = Literal["submit", "resume", "steer", "interrupt", "stop"]
InputRequestStatus = Literal["pending", "answered", "cancelled"]


class InputOption(Contract):
    """An optional selectable answer presented to the user."""

    value: str
    label: str


class InputRequest(Contract):
    """A request that lets a waiting Run continue."""

    request_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    options: list[InputOption] = Field(default_factory=list)
    allow_free_text: bool = True
    required: bool = True
    status: InputRequestStatus = "pending"
    answer: Any | None = None


class RunRecovery(Contract):
    """User-facing recovery choices for a terminal Run.

    Internal provider and context errors stay in the diagnostic Run state;
    the Workbench receives this actionable projection instead.
    """

    mode: RecoveryMode = "none"
    can_continue: bool = False
    can_retry: bool = False
    message: str | None = None


class RunEnvelope(Contract):
    """Scheduler-owned state for one user goal.

    ``account_id`` identifies the owner of account-scoped resources while
    ``session_id`` remains the stable conversation scheduler key.  It is
    optional for legacy, unauthenticated harness callers; authenticated
    workspace runs always populate it at submission time.

    ``payload`` stays open because the scheduler must not impose the business
    fields used by perception, materials, or dynamic RAG.
    """

    run_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    session_id: str = Field(min_length=1)
    # Public Workbench identity. ``session_id`` remains an internal Harness
    # routing key and is kept only for compatibility with existing callers.
    conversation_id: str | None = Field(default=None, min_length=1)
    account_id: str | None = Field(default=None, min_length=1)
    task_id: str | None = None
    payload: Any
    status: RunStatus = "queued"
    revision: int = 0
    pending_request_ids: list[str] = Field(default_factory=list)
    answers: dict[str, Any] = Field(default_factory=dict)
    output: str | None = None
    partial_output: str | None = None
    error: Error | None = None
    client_request_id: str | None = Field(default=None, min_length=1)
    retry_of_run_id: str | None = Field(default=None, min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    first_event_at: datetime | None = None
    first_delta_at: datetime | None = None
    completed_at: datetime | None = None
    last_sequence: int = 0
    completed_units: int | None = None
    total_units: int | None = None
    current_unit: str | None = None
    # Run recovery only. Public serialization must never contain evidence bodies.
    execution_state: dict[str, Any] = Field(default_factory=dict, exclude=True)


class MessageCommand(Contract):
    """A control-plane command received from an interface or worker."""

    kind: CommandKind
    session_id: str = Field(min_length=1)
    run_id: str | None = None
    request_id: str | None = None
    payload: Any | None = None


class DispatchResult(Contract):
    """The scheduler acknowledgement; it is not the final model answer."""

    session_id: str
    run_id: str
    status: RunStatus
    input_request_ids: list[str] = Field(default_factory=list)


class SessionSnapshot(Contract):
    """Read-only view used by tests and future admin/streaming adapters."""

    session_id: str
    status: SessionStatus = "open"
    active_run_id: str | None = None
    queued_run_ids: list[str] = Field(default_factory=list)
    waiting_run_ids: list[str] = Field(default_factory=list)
    runs: list[RunEnvelope] = Field(default_factory=list)


class PersistedSession(Contract):
    """Durable state required to reconstruct a scheduler session."""

    snapshot: SessionSnapshot
    input_requests: list[InputRequest] = Field(default_factory=list)
