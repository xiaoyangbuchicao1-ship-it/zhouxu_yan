from __future__ import annotations

from backend.contracts.modules.memory import TaskState, TaskWriteResult
from backend.contracts.ports import MemoryRepository


class TaskProgress:
    def __init__(self, repository: MemoryRepository):
        self.repository = repository

    async def update(
        self,
        state: TaskState,
        *,
        expected_revision: int | None,
    ) -> TaskWriteResult:
        step_ids = [step.step_id for step in state.steps]
        if len(step_ids) != len(set(step_ids)):
            return TaskWriteResult(
                accepted=False,
                conflict=True,
                notices=["duplicate_task_step_id"],
            )
        if state.current_step_id and state.current_step_id not in set(step_ids):
            return TaskWriteResult(
                accepted=False,
                conflict=True,
                notices=["unknown_current_step"],
            )
        return await self.repository.save_task(
            state,
            expected_revision=expected_revision,
        )
