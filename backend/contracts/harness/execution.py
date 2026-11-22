"""Private checkpoint shared by the loop, memory and lifecycle adapters."""

from typing import Any, Literal

from pydantic import Field

from backend.contracts.modules.memory import TaskState
from backend.contracts.schemas.core import Contract


class ExecutionState(Contract):
    schema_version: Literal[2] = 2
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
    context_compaction_count: int = Field(default=0, ge=0, strict=True)
    # Human-readable source cards. Internal retrieval identities remain inside
    # tool receipts and are never copied into this public projection.
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    # The currently activated declarative Skills are lightweight metadata only;
    # the full method body remains reloadable through read_skill. Keeping the
    # ID and version in the checkpoint lets compaction/recovery preserve which
    # method was in use without pinning a large document into every request.
    active_skills: list[dict[str, Any]] = Field(default_factory=list)
    # Follow-up instructions attached to an explicit Continue command. They
    # are durable checkpoint data, not a replacement for the original goal.
    continuation_guidance: list[str] = Field(default_factory=list)
    # Snapshot of deliverable files before the Run's first execution pass so
    # pause/continue cycles never lose files created in an earlier pass.
    workspace_baseline: dict[str, str] | None = None
    workspace_baseline_origin: Literal["run_start", "migration_cutover"] = "run_start"
    # Internal artifact metadata is projected into authorized download cards.
    # It deliberately keeps the storage path out of public API contracts.
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    model_continuation: dict[str, Any] = Field(default_factory=dict, repr=False)
