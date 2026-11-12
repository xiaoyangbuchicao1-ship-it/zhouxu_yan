import sys
from time import perf_counter

from backend.config.settings import load_settings
from backend.contracts.modules.memory import (
    MemoryInput,
    MemoryOutput,
    MemoryRecord,
)
from backend.contracts.ports import MemoryRepository
from backend.contracts.schemas.core import Error, ModuleRequest, ModuleResponse
from backend.infrastructure.database.in_memory_memory import InMemoryMemoryRepository
from backend.modules.memory.progress import TaskProgress
from backend.modules.memory.recall import DeterministicRecallPolicy

CANDIDATE_WINDOW_MULTIPLIER = 4


class InMemoryMemory:
    def __init__(
        self,
        repository: MemoryRepository | None = None,
        *,
        recall_policy: DeterministicRecallPolicy | None = None,
        max_chars: int | None = None,
        max_items: int | None = None,
        candidate_window_multiplier: int = CANDIDATE_WINDOW_MULTIPLIER,
        unified_budget: bool = False,
    ):
        self.repository = repository or InMemoryMemoryRepository()
        self.settings = load_settings()
        defaults = self.settings.memory
        self.recall_policy = recall_policy or DeterministicRecallPolicy()
        self.progress = TaskProgress(self.repository)
        self.max_chars = max_chars or defaults.max_recall_chars
        self.max_items = max_items or defaults.max_recall_items
        self.candidate_window_multiplier = candidate_window_multiplier
        self.unified_budget = unified_budget

    @staticmethod
    def _session_id(request: ModuleRequest[MemoryInput]) -> str:
        return request.input.session_id or f"ephemeral:{request.run_id}"

    @staticmethod
    def _coerce_record(
        item,
        *,
        session_id: str,
        run_id: str,
        task_id: str | None,
    ) -> MemoryRecord:
        record = MemoryRecord.model_validate(item).model_copy(deep=True)
        for field, expected in (
            ("session_id", session_id),
            ("run_id", run_id),
            ("task_id", task_id),
        ):
            actual = getattr(record, field)
            if actual is not None and actual != expected:
                raise ValueError("memory_scope")
        record.session_id = record.session_id or session_id
        record.run_id = record.run_id or run_id
        record.task_id = record.task_id or task_id
        return record

    async def handle(
        self, request: ModuleRequest[MemoryInput]
    ) -> ModuleResponse[MemoryOutput]:
        value = request.input
        session_id = self._session_id(request)
        task_id = value.task_id
        if value.operation == "restore":
            if any(r.session_id != session_id or r.run_id == request.run_id
                   or r.source not in {"user", "history"} for r in value.items):
                return ModuleResponse(status="failed", error=self.settings.error("memory_scope"))
            write = await self.repository.append(value.items)
            recall = await self._recall(request, session_id=session_id)
            return ModuleResponse(status="failed" if write.conflict_record_ids else "ok",
                                  output=self._output(recall, write=write),
                                  error=self.settings.error("memory_write_conflict") if write.conflict_record_ids else None)
        if value.operation == "record":
            try:
                records = [
                    self._coerce_record(
                        item,
                        session_id=session_id,
                        run_id=request.run_id,
                        task_id=task_id,
                    )
                    for item in value.items
                ]
            except ValueError:
                return ModuleResponse(
                    status="failed",
                    error=Error(
                        code="memory_scope",
                        message=self.settings.messages["memory_scope"],
                    ),
                )
            write = await self.repository.append(records)
            recall = await self._recall(request, session_id=session_id)
            return ModuleResponse(
                status="failed" if write.conflict_record_ids else "ok",
                output=self._output(recall, write=write),
                error=self.settings.error("memory_write_conflict")
                if write.conflict_record_ids
                else None,
            )
        if value.operation == "update_task":
            if value.task_state is None:
                return ModuleResponse(
                    status="needs_input",
                    error=self.settings.error("memory_task_required"),
                )
            state = value.task_state.model_copy(deep=True)
            if state.session_id != session_id or (task_id and state.task_id != task_id):
                return ModuleResponse(
                    status="failed",
                    error=self.settings.error("memory_scope"),
                )
            result = await self.progress.update(
                state,
                expected_revision=value.expected_revision,
            )
            recall = await self._recall(request, session_id=session_id)
            return ModuleResponse(
                status="ok" if result.accepted else "failed",
                output=self._output(recall, task_write=result),
                error=self.settings.error("memory_task_conflict")
                if not result.accepted
                else None,
            )
        if value.operation in {"close_run", "clear"}:
            await self.repository.close_run(
                session_id=session_id,
                run_id=request.run_id,
                purge_session=value.purge_session,
            )
            recall = await self._recall(request, session_id=session_id)
            return ModuleResponse(
                status="ok", output=self._output(recall, closed_run=True)
            )
        if value.operation == "recall":
            recall = await self._recall(request, session_id=session_id)
            return ModuleResponse(
                # Budget pressure is recoverable by dropping optional layers.
                # A missing reference or unfinished active exchange is an
                # internal protocol failure, never a user clarification.
                status=(
                    "failed"
                    if recall.blocked_reason == "required_context_unavailable"
                    else "ok"
                ),
                output=recall,
                error=self.settings.error(recall.blocked_reason, retryable=True)
                if recall.blocked_reason == "required_context_unavailable"
                else None,
            )
        return ModuleResponse(
            status="failed",
            error=self.settings.error("memory_operation"),
        )

    @staticmethod
    def _output(recall, **updates) -> MemoryOutput:
        return recall.model_copy(update=updates)

    async def _recall(
        self,
        request: ModuleRequest[MemoryInput],
        *,
        session_id: str,
    ):
        value = request.input
        began = perf_counter()
        item_limit = value.max_items or self.max_items
        candidate_limit = (
            item_limit * self.candidate_window_multiplier if item_limit else None
        )
        records = await self.repository.list_records(
            session_id=session_id,
            limit=candidate_limit,
        )
        current_records = await self.repository.list_records(
            session_id=session_id,
            run_id=request.run_id,
        )
        reference_records = (
            await self.repository.list_records(
                session_id=session_id,
                record_ids=value.reference_ids,
            )
            if value.reference_ids
            else []
        )
        by_id = {record.record_id: record for record in records}
        by_id.update({record.record_id: record for record in current_records})
        by_id.update({record.record_id: record for record in reference_records})
        required_records = await self.repository.list_records(
            session_id=session_id, kinds=self.recall_policy.required_kinds
        )
        by_id.update(
            {
                r.record_id: r
                for r in required_records
                if r.status == "available"
                and (r.task_id is None or r.task_id == value.task_id)
            }
        )
        partners = await self.repository.list_records(
            session_id=session_id, related_to=list(by_id)
        )
        by_id.update({r.record_id: r for r in partners})
        records = sorted(
            by_id.values(), key=lambda item: (item.created_at, item.record_id)
        )
        task_state = None
        if value.task_id:
            task_state = await self.repository.get_task(
                session_id=session_id,
                task_id=value.task_id,
            )
        recall = self.recall_policy.select(
            records,
            session_id=session_id,
            run_id=request.run_id,
            task_id=value.task_id,
            task_state=task_state,
            reference_ids=value.reference_ids,
            max_items=None
            if self.unified_budget
            else value.max_items or self.max_items,
            # Production hands complete candidates to perception. The recall
            # ranking, retention and item resource window are unchanged.
            max_chars=sys.maxsize
            if self.unified_budget
            else value.max_chars or self.max_chars,
        )
        try:
            stored_count = await self.repository.count_session_records(
                session_id=session_id
            )
        except Exception:
            # Diagnostic counts are optional; repository data reads above are not.
            stored_count = None
        recall.trace.update(
            {
                "stored_count": stored_count,
                "candidate_window_limit": candidate_limit,
                "budget_handoff": "full_request_perception"
                if self.unified_budget
                else "standalone_resource_limit",
                "outside_window_count": max(0, stored_count - len(by_id))
                if stored_count is not None
                else None,
                "elapsed_ms": round((perf_counter() - began) * 1000, 3),
            }
        )
        return recall
