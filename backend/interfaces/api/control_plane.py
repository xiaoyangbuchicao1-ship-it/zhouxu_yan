"""Application service for the Harness lifecycle HTTP adapter."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from backend.contracts.harness.lifecycle import (
    DispatchResult,
    RunEnvelope,
    SessionSnapshot,
)
from backend.contracts.modules.loop import RunInput
from backend.contracts.ports import RunStore
from backend.harness.lifecycle.scheduler import (
    InMemoryRunScheduler,
    SchedulerCommandError,
)


class HarnessControlService:
    """Thin service boundary between HTTP and the lifecycle scheduler.

    The service owns no business rules. It only converts the public request
    shape into the scheduler's stable contracts and keeps the scheduler
    replaceable for a distributed worker implementation later.
    """

    def __init__(self, executor, *, store: RunStore | None = None):
        self.scheduler = InMemoryRunScheduler(executor, store)

    async def submit(
        self,
        *,
        session_id: str,
        payload: RunInput,
        task_id: str | None = None,
        account_id: str | None = None,
        client_request_id: str | None = None,
        retry_of_run_id: str | None = None,
        conversation_id: str | None = None,
    ) -> tuple[DispatchResult, RunEnvelope]:
        dispatch = await self.scheduler.submit(
            session_id=session_id,
            payload=payload,
            task_id=task_id,
            account_id=account_id,
            client_request_id=client_request_id,
            retry_of_run_id=retry_of_run_id,
            conversation_id=conversation_id,
        )
        return dispatch, await self.scheduler.get_run(
            session_id=session_id, run_id=dispatch.run_id
        )

    async def set_partial(
        self, *, session_id: str, run_id: str, partial_output: str | None
    ) -> RunEnvelope:
        return await self.scheduler.set_partial(
            session_id=session_id, run_id=run_id, partial_output=partial_output
        )

    async def continue_run(self, *, session_id: str, run_id: str):
        dispatch = await self.scheduler.continue_run(session_id=session_id, run_id=run_id)
        return dispatch, await self.scheduler.get_run(session_id=session_id, run_id=run_id)

    async def resume(
        self,
        *,
        session_id: str,
        run_id: str,
        request_id: str,
        answer: Any,
        payload: RunInput | None = None,
    ) -> tuple[DispatchResult, RunEnvelope]:
        dispatch = await self.scheduler.resume(
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            answer=answer,
            payload=payload,
        )
        return dispatch, await self.scheduler.get_run(
            session_id=session_id, run_id=run_id
        )

    async def steer(
        self, *, session_id: str, run_id: str, payload: RunInput
    ) -> tuple[DispatchResult, RunEnvelope]:
        existing = await self.run(session_id=session_id, run_id=run_id)
        original = RunInput.model_validate(existing.payload)
        facts = dict(original.facts)
        facts.update(payload.facts)
        updates = dict(facts.get("user_answers", {}))
        updates["steer:" + uuid4().hex] = payload.text
        facts["user_answers"] = updates
        payload = original.model_copy(update={"facts": facts, "material_ids": payload.material_ids or original.material_ids, "constraints": list(dict.fromkeys(original.constraints + payload.constraints)), "attached_material_ids": list(dict.fromkeys(original.attached_material_ids + payload.attached_material_ids))})
        dispatch = await self.scheduler.steer(
            session_id=session_id, run_id=run_id, payload=payload
        )
        return dispatch, await self.scheduler.get_run(
            session_id=session_id, run_id=run_id
        )

    async def interrupt(
        self, *, session_id: str, run_id: str
    ) -> tuple[DispatchResult, RunEnvelope]:
        dispatch = await self.scheduler.interrupt(
            session_id=session_id, run_id=run_id
        )
        return dispatch, await self.scheduler.get_run(
            session_id=session_id, run_id=run_id
        )

    async def stop(
        self, *, session_id: str, run_id: str
    ) -> tuple[DispatchResult, RunEnvelope]:
        dispatch = await self.scheduler.stop(session_id=session_id, run_id=run_id)
        return dispatch, await self.scheduler.get_run(
            session_id=session_id, run_id=run_id
        )

    async def run(self, *, session_id: str, run_id: str) -> RunEnvelope:
        return await self.scheduler.get_run(session_id=session_id, run_id=run_id)

    async def request(self, *, session_id: str, request_id: str):
        return await self.scheduler.get_input_request(
            session_id=session_id, request_id=request_id
        )

    async def session(self, *, session_id: str) -> SessionSnapshot:
        return await self.scheduler.snapshot(session_id=session_id)

    async def wait_for_idle(self, *, session_id: str) -> None:
        await self.scheduler.wait_for_idle(session_id=session_id)


class UnavailableRunExecutor:
    """Safe startup fallback when model credentials are not configured."""

    def __init__(self, error):
        self.error = error

    async def execute(self, run, control):
        from backend.contracts.modules.loop import RunResult

        return RunResult(
            run_id=run.run_id,
            status="failed",
            error=self.error,
            elapsed_seconds=0,
        )


__all__ = [
    "HarnessControlService",
    "SchedulerCommandError",
    "UnavailableRunExecutor",
]

