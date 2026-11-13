"""Private checkpoint shared by the loop, memory and lifecycle adapters."""

from typing import Any

from pydantic import Field

from backend.contracts.modules.memory import TaskState
from backend.contracts.schemas.core import Contract


class ExecutionState(Contract):
    schema_version: int = 1
    records: list[dict[str, Any]] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    task_state: TaskState | None = None
    compacted_record_ids: list[str] = Field(default_factory=list)
    tool_counts: dict[str, int] = Field(default_factory=dict)
    rounds_completed: int = 0
    material_coverage: list[dict[str, Any]] = Field(default_factory=list)
    stop_reason: str | None = None
    context_resources: list[dict[str, Any]] = Field(default_factory=list)
    delivered_stages: list[str] = Field(default_factory=list)
    remaining_work: list[str] = Field(default_factory=list)
    material_statuses: dict[str, str] = Field(default_factory=dict)
    # A user continuation can request one bounded recovery pass without
    # changing the semantic task checkpoint or replaying tool side effects.
    recovery_mode: str | None = None
    recovery_attempts: int = 0
    compaction_requested: bool = False
    # Human-readable source cards. Internal retrieval identities remain inside
    # tool receipts and are never copied into this public projection.
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    # The currently activated declarative Skills are lightweight metadata only;
    # the full method body remains reloadable through read_skill. Keeping the
    # ID and version in the checkpoint lets compaction/recovery preserve which
    # method was in use without pinning a large document into every request.
    active_skills: list[dict[str, str]] = Field(default_factory=list)
    # Internal artifact metadata is projected into authorized download cards.
    # It deliberately keeps the storage path out of public API contracts.
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    model_continuation: dict[str, Any] = Field(default_factory=dict, repr=False)
