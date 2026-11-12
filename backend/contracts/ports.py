from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from backend.contracts.events.runtime import RunEvent
from backend.contracts.events.streaming import StreamEvent
from backend.contracts.harness.lifecycle import PersistedSession
from backend.contracts.model_context import RequestBudget, RequestEstimate
from backend.contracts.modules.dynamic_rag import (
    Evidence,
    EvidenceReview,
    RetrievalFilters,
    SearchBatch,
    SearchPlan,
)
from backend.contracts.modules.loop import (
    ActionRequest,
    ActionResult,
    DecisionProposal,
    ReasoningInput,
    ToolSpec,
)
from backend.contracts.modules.memory import (
    MemoryRecord,
    MemoryWriteResult,
    TaskState,
    TaskWriteResult,
)
from backend.contracts.modules.perception import ContextItem
from backend.contracts.schemas.core import ModuleRequest, ModuleResponse


class Module(Protocol):
    async def handle(self, request: ModuleRequest[Any]) -> ModuleResponse[Any]: ...


class ModelPort(Protocol):
    async def decide(
        self, request: ModuleRequest[ReasoningInput]
    ) -> DecisionProposal: ...


@runtime_checkable
class ModelContextPort(Protocol):
    """Complete-request counting/encoding belongs to the current model adapter."""

    def context_budget(self) -> RequestBudget: ...

    def estimate_context(
        self, items: list[ContextItem], tools: list[ToolSpec], *, run_id: str
    ) -> RequestEstimate: ...

    def supports_images(self) -> bool: ...

    def allocate_tool_context(
        self, used_tokens: int, call_count: int, image_count: int = 0
    ) -> dict[str, int]: ...


class StreamingModelPort(Protocol):
    async def decide_stream(
        self,
        request: ModuleRequest[ReasoningInput],
        on_delta: Callable[[str], Awaitable[None]],
    ) -> DecisionProposal: ...


@runtime_checkable
class ModelContinuationPort(Protocol):
    """Opaque private protocol state; not semantic memory or public trace."""

    def export_run_state(self, run_id: str) -> dict: ...

    def restore_run_state(self, run_id: str, value: dict) -> None: ...

    def close_run(self, run_id: str) -> None: ...


class ToolExecutor(Protocol):
    async def execute(self, request: ActionRequest) -> ActionResult: ...


@runtime_checkable
class ContextualToolExecutor(Protocol):
    """Optional extension for tools needing trusted Harness correlation IDs."""

    async def execute_context(
        self, request: ModuleRequest[ActionRequest]
    ) -> ActionResult: ...


class ExecutionProvider(Protocol):
    """Stable work-mode boundary shared by Docker and native providers."""

    def registrations(
        self,
    ) -> dict[str, tuple[ToolSpec, ContextualToolExecutor]]: ...

    async def execute(
        self, name: str, request: ModuleRequest[ActionRequest]
    ) -> ActionResult: ...

    def collect_artifacts(self, run_id: str, *, completed: bool) -> list[dict]: ...

    async def close_run(self, run_id: str) -> None: ...

    async def close_all(self) -> None: ...


class EventSink(Protocol):
    def emit(self, event: RunEvent) -> None: ...


class StreamSink(Protocol):
    def emit_stream(self, event: StreamEvent) -> None: ...


class RunStore(Protocol):
    async def load(self, *, session_id: str) -> PersistedSession | None: ...

    async def save(self, state: PersistedSession) -> None: ...


class MemoryRepository(Protocol):
    async def append(self, records: list[MemoryRecord]) -> MemoryWriteResult: ...

    async def list_records(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
        task_id: str | None = None,
        limit: int | None = None,
        record_ids: list[str] | None = None,
        kinds: tuple[str, ...] | None = None,
        related_to: list[str] | None = None,
    ) -> list[MemoryRecord]: ...

    async def count_session_records(self, *, session_id: str) -> int: ...

    async def get_task(self, *, session_id: str, task_id: str) -> TaskState | None: ...

    async def save_task(
        self, state: TaskState, *, expected_revision: int | None
    ) -> TaskWriteResult: ...

    async def close_run(
        self, *, session_id: str, run_id: str, purge_session: bool = False
    ) -> int: ...


class QueryEmbeddingPort(Protocol):
    async def encode(self, text: str) -> list[float]: ...


class SearchPort(Protocol):
    async def search(
        self, query: str | SearchPlan, filters: RetrievalFilters, limit: int
    ) -> SearchBatch: ...


class EvidenceReviewPort(Protocol):
    async def review(
        self,
        query: str,
        evidence: list[Evidence],
        previous_queries: list[str],
        filters: RetrievalFilters,
    ) -> EvidenceReview: ...
