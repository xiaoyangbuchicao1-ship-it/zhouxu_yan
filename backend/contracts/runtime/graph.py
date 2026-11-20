"""Business-neutral contracts for a resumable execution graph.

The graph layer deliberately contains no maritime or paper-specific fields. A
skill, tool, model, file operation, or human gate is represented by the same
node contract and can therefore be observed and recovered in one Run.
"""

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from backend.contracts.schemas.core import Contract, Error

NodeKind = Literal[
    "skill",
    "tool",
    "model",
    "file",
    "human_gate",
    "compile",
    "subgraph",
    "transform",
]
NodeStatus = Literal[
    "queued",
    "preparing",
    "running",
    "waiting_user",
    "pausing",
    "paused",
    "resuming",
    "retrying",
    "succeeded",
    "failed",
    "cancelled",
]
StandardRunStatus = Literal[
    "queued",
    "preparing",
    "running",
    "waiting_user",
    "pausing",
    "paused",
    "resuming",
    "retrying",
    "succeeded",
    "failed",
    "cancelled",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def standard_run_status(value: str) -> StandardRunStatus:
    """Map legacy scheduler values to the public lifecycle vocabulary."""

    return {
        "completed": "succeeded",
        "interrupted": "paused",
        "stopped": "cancelled",
    }.get(value, value)  # type: ignore[return-value]


def canonical_run_status(value: str) -> StandardRunStatus:
    """Compatibility name used by the first lifecycle migration draft."""

    return standard_run_status(value)


class Ref(Contract):
    """A permission-scoped pointer to a value or file, never the value itself."""

    ref_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    kind: Literal["input", "output", "artifact", "checkpoint", "event"] = "output"
    name: str = Field(min_length=1)
    media_type: str = "application/json"
    sha256: str | None = None


class RetryPolicy(Contract):
    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_ms: int = Field(default=0, ge=0)
    retryable_codes: list[str] = Field(default_factory=list)


class RunPolicy(Contract):
    max_concurrency: int = Field(default=1, ge=1, le=32)
    timeout_ms: int | None = Field(default=None, ge=1)
    allow_external_side_effects: bool = False


class NodeSpec(Contract):
    id: str = Field(min_length=1)
    kind: NodeKind
    label: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    input_refs: list[Ref] = Field(default_factory=list)
    config_ref: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_ms: int | None = Field(default=None, ge=1)
    side_effect: Literal["none", "idempotent", "external"] = "none"
    visibility: Literal["visible", "compact", "internal"] = "visible"


class Edge(Contract):
    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    from_node: str = Field(min_length=1)
    to_node: str = Field(min_length=1)
    condition: str | None = None


class RunGraph(Contract):
    graph_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    version: str = Field(default="1", min_length=1)
    nodes: list[NodeSpec] = Field(min_length=1)
    edges: list[Edge] = Field(default_factory=list)


class RunSpec(Contract):
    """Immutable run intent captured when a graph is submitted."""

    goal: str = Field(min_length=1)
    graph: RunGraph
    policy: RunPolicy = Field(default_factory=RunPolicy)
    created_at: datetime = Field(default_factory=now_utc)


class NodeState(Contract):
    node_id: str = Field(min_length=1)
    kind: NodeKind
    label: str = Field(min_length=1)
    status: NodeStatus = "queued"
    attempt: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    input_refs: list[Ref] = Field(default_factory=list)
    output_refs: list[Ref] = Field(default_factory=list)
    checkpoint_ref: str | None = None
    error: Error | None = None


class Attempt(Contract):
    attempt_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    run_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    number: int = Field(ge=1)
    started_at: datetime = Field(default_factory=now_utc)
    finished_at: datetime | None = None
    status: NodeStatus = "running"
    idempotency_key: str = Field(min_length=1)
    provider_request_id: str | None = None
    error: Error | None = None


class Checkpoint(Contract):
    checkpoint_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    run_id: str = Field(min_length=1)
    graph_version: str = Field(min_length=1)
    completed_node_ids: list[str] = Field(default_factory=list)
    active_node_id: str | None = None
    pending_node_ids: list[str] = Field(default_factory=list)
    event_sequence: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=now_utc)


class Artifact(Contract):
    artifact_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    run_id: str = Field(min_length=1)
    node_id: str | None = None
    name: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    size_bytes: int = Field(default=0, ge=0)
    sha256: str = Field(min_length=1)
    uri: str | None = None


class GraphNodeResult(Contract):
    output_refs: list[Ref] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    provider_request_id: str | None = None
    summary: str | None = None


class GraphProgress(Contract):
    """A full node projection handed back to the owning scheduler."""

    node_states: dict[str, NodeState] = Field(default_factory=dict)
    attempts: list[Attempt] = Field(default_factory=list)
    checkpoint: Checkpoint | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    current_node_id: str | None = None
    current_attempt_id: str | None = None
    elapsed_ms: int = Field(default=0, ge=0)
    state: dict[str, Any] = Field(default_factory=dict)
