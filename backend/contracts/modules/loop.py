from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from backend.contracts.modules.materials import MaterialInput
from backend.contracts.modules.memory import MemoryInput as MemoryInput  # noqa: F401
from backend.contracts.modules.memory import MemoryRecall
from backend.contracts.modules.perception import ContextAssembly, ContextItem
from backend.contracts.schemas.core import Contract, Error

ThinkingMode = Literal["none", "low", "high"]
ConversationMode = Literal["chat", "work"]


class RunInput(Contract):
    text: str
    # Conversation mode is copied into the Run at submission time.  Keeping
    # it on the immutable Run payload prevents a later conversation switch
    # from changing the permissions of work that is already queued or running.
    mode: ConversationMode | None = None
    # A Run owns its mode; None only supports older checkpoints/CLI inputs.
    thinking_mode: ThinkingMode | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    materials: list[MaterialInput] = Field(default_factory=list)
    # Public Harness submissions carry opaque uploaded-material IDs. The
    # executor resolves them to server-local MaterialInput values before the
    # parser runs; raw storage paths never cross the HTTP boundary.
    material_ids: list[str] = Field(default_factory=list)
    # Message attachment identity is separate from the material candidate scope.
    attached_material_ids: list[str] = Field(default_factory=list)


class FeedbackRecord(Contract):
    record_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    source: str = Field(min_length=1)
    summary: str = ""
    updates: dict[str, Any] = Field(default_factory=dict)


MemoryContext = MemoryRecall


class PerceptionInput(Contract):
    current: RunInput
    memory: MemoryRecall
    candidates: list[ContextItem] = Field(default_factory=list)
    required_candidate_ids: list[str] = Field(default_factory=list)
    tools: list["ToolSpec"] = Field(default_factory=list)


class PerceivedContext(Contract):
    run_id: str = Field(min_length=1)
    items: list[ContextItem] = Field(default_factory=list)
    assembly: ContextAssembly
    needs_compaction: bool = False

    # Compatibility accessors keep the first vertical slice readable while the
    # model-facing contract is now the typed ``items`` collection.
    @property
    def goal(self) -> str:
        current = next((item for item in self.items if item.id == "current-user"), None)
        return str((current.body if current else {}).get("goal", ""))

    @property
    def facts(self) -> dict[str, Any]:
        current = next((item for item in self.items if item.id == "current-user"), None)
        facts = (current.body if current else {}).get("facts", {})
        return facts if isinstance(facts, dict) else {}

    @property
    def constraints(self) -> list[str]:
        current = next((item for item in self.items if item.id == "current-user"), None)
        constraints = (current.body if current else {}).get("constraints", [])
        return constraints if isinstance(constraints, list) else []

    @property
    def records(self) -> list[FeedbackRecord]:
        """Read-only compatibility view for the first-slice test doubles.

        The model-facing contract is ``items``. This projection is deliberately
        not serialized and can be removed once callers have migrated.
        """

        records: list[FeedbackRecord] = []
        for item in self.items:
            if item.source == "harness" and item.kind == "context.inventory":
                continue  # A derived index is not a historical operation.
            if item.id == "current-user":
                continue
            if item.type == "tool_call":
                proposal = DecisionProposal(
                    kind="action",
                    action_requests=item.body.get("calls", []),
                )
                records.append(
                    FeedbackRecord(
                        record_id=item.id,
                        source="reasoning",
                        updates={"proposal": proposal.model_dump()},
                    )
                )
            elif item.type == "tool_result":
                action = ActionResult.model_validate(item.body)
                records.append(
                    FeedbackRecord(
                        record_id=item.id,
                        source="action",
                        updates={"action": action.model_dump()},
                    )
                )
            elif item.type == "message":
                body = item.body
                source = (
                    item.source
                    if item.source in {"reflection", "user"}
                    else "reasoning"
                )
                records.append(
                    FeedbackRecord(
                        record_id=item.id,
                        source=source,
                        summary=str(body.get("summary", body.get("text", ""))),
                        updates=dict(body.get("updates", {})),
                    )
                )
        return records


class ActionRequest(Contract):
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any]
    action_id: str = Field(default_factory=lambda: uuid4().hex)


class ActionResult(Contract):
    tool_name: str
    action_id: str
    result: Any
    # Transport success means a receipt was obtained, not that the operation
    # succeeded. Defaults keep existing tools and checkpoints readable.
    outcome: Literal["ok", "partial", "empty", "error"] = "ok"
    error: Error | None = None
    side_effects: list[str] = Field(default_factory=list)
    resource_id: str | None = None
    # If provided, full output is run-only and this projection is kept in the
    # session. It is not duplicated in provider tool-result payloads.
    session_summary: dict[str, Any] | None = Field(default=None, exclude=True)


class DecisionProposal(Contract):
    kind: Literal["answer", "action", "needs_input"]
    answer: str | None = None
    commentary: str | None = None
    action_requests: list[ActionRequest] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    finish_reason: str | None = None
    incomplete: bool = False
    complete: bool = True
    remaining_work: list[str] = Field(default_factory=list)
    blocked: bool = False


class ToolSpec(Contract):
    name: str
    description: str
    parameters: dict[str, Any]
    # Management classification only; all model-callable capabilities still
    # use the same ActionRequest/ActionResult protocol.  ``skill_activation``
    # is used by read_skill, while business and execution tools retain their
    # existing behavior.
    kind: Literal["business", "skill_activation", "control", "execution"] = "business"
    # Conservative default: unknown executors may have external side effects.
    read_only: bool = False
    # Opt in only for tools whose identical observations can be reused within
    # this Run. Polling/live-data tools retain the conservative default.
    reuse_identical_results: bool = False


class ReasoningInput(Contract):
    perceived: PerceivedContext
    tools: list[ToolSpec] = Field(default_factory=list)
    purpose: Literal["main", "compaction", "finalization"] = "main"
    # Display-only prefix: already committed stages, not a second model prompt.
    delivered_prefix: str = ""


class ReflectionInput(Contract):
    proposed: DecisionProposal
    actions: list[ActionResult] = Field(default_factory=list)


class ReflectionResult(Contract):
    decision: Literal["complete", "continue", "needs_input", "retry", "fail"]
    issues: list[str] = Field(default_factory=list)
    feedback: str = ""


class RunResult(Contract):
    run_id: str
    status: Literal["completed", "needs_input", "failed", "cancelled", "yielded"]
    answer: str | None = None
    missing_inputs: list[str] = Field(default_factory=list)
    error: Error | None = None
    elapsed_seconds: float
    completed_units: int | None = None
    total_units: int | None = None
    current_unit: str | None = None
    execution_state: dict[str, Any] = Field(default_factory=dict, exclude=True)


PerceptionInput.model_rebuild()
