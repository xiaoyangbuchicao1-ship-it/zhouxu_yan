"""In-memory Harness scheduler for the first lifecycle vertical slice.

The scheduler owns Session/Run routing. The existing Harness remains the
executor for one Run; persistence and parallel workers are intentionally left
behind a later port.
"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend.contracts.harness.lifecycle import (
    DispatchResult,
    InputRequest,
    PersistedSession,
    RunEnvelope,
    RunStatus,
    SessionSnapshot,
)
from backend.contracts.modules.loop import RunInput, RunResult
from backend.contracts.ports import RunStore
from backend.contracts.schemas.core import Error
from backend.harness.lifecycle.control import RunControl
from backend.harness.lifecycle.recovery import (
    prepare_checkpoint_for_continue,
    recovery_for_run,
)


class RunExecutor(Protocol):
    async def execute(self, run: RunEnvelope, control: RunControl) -> RunResult: ...


class SchedulerCommandError(ValueError):
    """The command cannot be applied to the requested lifecycle state."""


@dataclass
class _SessionRuntime:
    session_id: str
    runs: dict[str, RunEnvelope] = field(default_factory=dict)
    requests: dict[str, InputRequest] = field(default_factory=dict)
    queue: deque[str] = field(default_factory=deque)
    active_run_id: str | None = None
    controls: dict[str, RunControl] = field(default_factory=dict)
    tasks: dict[str, asyncio.Task] = field(default_factory=dict)


class InMemoryRunScheduler:
    """Single-active-Run scheduler with explicit waiting and cancellation.

    This is deliberately process-local. A later persistence port can replace
    the runtime store without changing the lifecycle contracts.
    """

    def __init__(self, executor: RunExecutor, store: RunStore | None = None, on_change=None):
        self._executor = executor
        self._store = store
        self._sessions: dict[str, _SessionRuntime] = {}
        self._lock = asyncio.Lock()
        self.on_change = on_change

    async def recover(self) -> None:
        """Recover accepted work at startup, without requiring a browser visit."""
        discover = getattr(self._store, "list_sessions", None)
        if discover:
            for session_id in await discover():
                await self._ensure_session(session_id)

    async def shutdown(self) -> None:
        # Cancel execution tasks without turning running work into a user stop.
        async with self._lock:
            tasks = [task for session in self._sessions.values() for task in session.tasks.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def continue_run(self, *, session_id: str, run_id: str) -> DispatchResult:
        await self._ensure_session(session_id)
        async with self._lock:
            session = self._require_session(session_id)
            run = self._require_run(session, run_id)
            if run.status in {"running", "queued"}:
                return self._result_locked(session, run)
            if run.status not in {"stopped", "interrupted", "failed"}:
                raise SchedulerCommandError("run cannot be continued")
            recovery = recovery_for_run(run)
            if not recovery.can_continue:
                raise SchedulerCommandError("run cannot be continued")
            still_stopping = run_id in session.tasks
            error_code = run.error.code if run.error else None
            run.status, run.error, run.completed_at = "queued", None, None
            run.execution_state = prepare_checkpoint_for_continue(
                run.execution_state, error_code=error_code
            )
            if run.partial_output:
                run.execution_state["resume_visible_output"] = run.partial_output
            run.revision += 1
            for key in list(run.pending_request_ids):
                session.requests[key].status = "cancelled"
            run.pending_request_ids = []
            if not still_stopping:
                session.queue.appendleft(run_id)
            await self._persist_locked(session)
        await self._dispatch(session_id)
        return self._result(session_id, run_id)

    async def submit(
        self,
        *,
        session_id: str,
        payload: Any,
        task_id: str | None = None,
        account_id: str | None = None,
        client_request_id: str | None = None,
        retry_of_run_id: str | None = None,
        conversation_id: str | None = None,
    ) -> DispatchResult:
        await self._ensure_session(session_id)
        async with self._lock:
            session = self._session(session_id)
            if client_request_id:
                for existing in session.runs.values():
                    if (
                        existing.client_request_id == client_request_id
                        and existing.account_id == account_id
                    ):
                        return self._result_locked(session, existing)
            if retry_of_run_id is not None:
                previous = self._require_run(session, retry_of_run_id)
                if previous.account_id != account_id:
                    raise SchedulerCommandError("retry source is not accessible")
            run = RunEnvelope(
                session_id=session_id,
                conversation_id=conversation_id,
                account_id=account_id,
                task_id=task_id,
                payload=payload,
                client_request_id=client_request_id,
                retry_of_run_id=retry_of_run_id,
            )
            session.runs[run.run_id] = run
            session.queue.append(run.run_id)
            await self._persist_locked(session)
        await self._dispatch(session_id)
        return self._result(session_id, run.run_id)

    async def set_partial(
        self, *, session_id: str, run_id: str, partial_output: str | None
    ) -> RunEnvelope:
        """Persist the visible answer accumulated before cancellation/failure."""
        await self._ensure_session(session_id)
        async with self._lock:
            session = self._require_session(session_id)
            run = self._require_run(session, run_id)
            if partial_output is not None and partial_output != "":
                run.partial_output = partial_output
                # A terminal run must never expose an unfinished answer as its
                # final output. Keep output separate until the executor finishes.
                if run.status != "completed":
                    run.output = None
                # Text persistence is not a new execution generation.
                await self._persist_locked(session)
            return run.model_copy(deep=True)

    async def resume(
        self,
        *,
        session_id: str,
        run_id: str,
        request_id: str,
        answer: Any,
        payload: Any | None = None,
    ) -> DispatchResult:
        await self._ensure_session(session_id)
        async with self._lock:
            session = self._require_session(session_id)
            run = self._require_run(session, run_id)
            request = session.requests.get(request_id)
            if run.status != "waiting_user" or request is None:
                raise SchedulerCommandError("run is not waiting for this request")
            if request.run_id != run_id or request.status != "pending":
                raise SchedulerCommandError("input request is not pending")
            request.status = "answered"
            request.answer = answer
            run.answers[request_id] = answer
            if payload is not None:
                run.payload = payload
            run.pending_request_ids.remove(request_id)
            run.revision += 1
            if run.pending_request_ids:
                await self._persist_locked(session)
                return self._result_locked(session, run)
            run.status = "queued"
            session.queue.appendleft(run_id)
            await self._persist_locked(session)
        await self._dispatch(session_id)
        return self._result(session_id, run_id)

    async def steer(
        self, *, session_id: str, run_id: str, payload: Any
    ) -> DispatchResult:
        await self._ensure_session(session_id)
        async with self._lock:
            session = self._require_session(session_id)
            run = self._require_run(session, run_id)
            if run.status != "running" or session.active_run_id != run_id:
                raise SchedulerCommandError("only the active Run can be steered")
            if isinstance(payload, RunInput):
                original = RunInput.model_validate(run.payload)
                payload = payload.model_copy(
                    update={
                        "thinking_mode": original.thinking_mode,
                        "mode": original.mode,
                    }
                )
            run.payload = payload
            run.revision += 1
            control = session.controls[run_id]
            control.cancel("interrupt")
            await self._persist_locked(session)
            return self._result_locked(session, run)

    async def interrupt(self, *, session_id: str, run_id: str) -> DispatchResult:
        return await self._cancel(
            session_id=session_id, run_id=run_id, terminal="interrupted"
        )

    async def stop(self, *, session_id: str, run_id: str) -> DispatchResult:
        return await self._cancel(
            session_id=session_id, run_id=run_id, terminal="stopped"
        )

    async def get_run(self, *, session_id: str, run_id: str) -> RunEnvelope:
        await self._ensure_session(session_id)
        async with self._lock:
            session = self._require_session(session_id)
            return self._require_run(session, run_id).model_copy(deep=True)

    async def get_input_request(
        self, *, session_id: str, request_id: str
    ) -> InputRequest:
        await self._ensure_session(session_id)
        async with self._lock:
            session = self._require_session(session_id)
            request = session.requests.get(request_id)
            if request is None:
                raise SchedulerCommandError("input request not found")
            return request.model_copy(deep=True)

    async def snapshot(self, *, session_id: str) -> SessionSnapshot:
        await self._ensure_session(session_id)
        async with self._lock:
            session = self._require_session(session_id)
            return SessionSnapshot(
                session_id=session_id,
                active_run_id=session.active_run_id,
                queued_run_ids=list(session.queue),
                waiting_run_ids=[
                    run_id
                    for run_id, run in session.runs.items()
                    if run.status == "waiting_user"
                ],
                runs=[run.model_copy(deep=True) for run in session.runs.values()],
            )

    async def wait_for_idle(self, *, session_id: str) -> None:
        """Wait until this session has no active execution task or queued Run."""
        await self._ensure_session(session_id)
        while True:
            async with self._lock:
                session = self._require_session(session_id)
                tasks = list(session.tasks.values())
                pending_queue = bool(session.queue)
            if not tasks and not pending_queue:
                return
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                await asyncio.sleep(0)

    async def _cancel(
        self, *, session_id: str, run_id: str, terminal: RunStatus
    ) -> DispatchResult:
        await self._ensure_session(session_id)
        async with self._lock:
            session = self._require_session(session_id)
            run = self._require_run(session, run_id)
            if run.status in {"completed", "failed", "interrupted", "stopped"}:
                return self._result_locked(session, run)
            run.status = terminal
            run.completed_at = datetime.now(timezone.utc)
            run.revision += 1
            if run_id in session.queue:
                session.queue.remove(run_id)
            control = session.controls.get(run_id)
            if control is not None:
                control.cancel("stop" if terminal == "stopped" else "interrupt")
            should_dispatch = session.active_run_id != run_id
            await self._persist_locked(session)
        if should_dispatch:
            await self._dispatch(session_id)
        return self._result(session_id, run_id)

    async def _execute(self, session_id: str, run_id: str, control: RunControl) -> None:
        async with self._lock:
            session = self._require_session(session_id)
            run = self._require_run(session, run_id)
            started_revision = run.revision
            execution_input = run.model_copy(deep=True)

        async def checkpoint(state):
            async with self._lock:
                current = self._require_run(session, run_id)
                if session.controls.get(run_id) is not control:
                    return
                current.execution_state = state
                await self._persist_locked(session)

        control.checkpoint = checkpoint
        try:
            outcome = await self._executor.execute(execution_input, control)
        except asyncio.CancelledError as exc:
            if asyncio.current_task().cancelling():
                # Service shutdown: retain running + latest checkpoint for restart.
                async with self._lock:
                    await self._persist_locked(session)
                return
            outcome = getattr(exc, "run_result", None) or RunResult(
                run_id=run_id,
                status="cancelled",
                error=Error(code="cancelled", message="run cancelled"),
                elapsed_seconds=0,
            )
        except Exception:
            outcome = RunResult(
                run_id=run_id,
                status="failed",
                error=Error(code="executor_failed", message="run executor failed"),
                elapsed_seconds=0,
            )

        async with self._lock:
            session = self._require_session(session_id)
            run = self._require_run(session, run_id)
            stale = run.revision != started_revision
            if stale:
                if outcome.execution_state:
                    run.execution_state = outcome.execution_state
                # A steer superseded this execution. Its result is never
                # published; the same Run is placed ahead of ordinary queue work.
                if run.status not in {"stopped", "interrupted"}:
                    run.status = "queued"
                    session.queue.appendleft(run_id)
                elif outcome.execution_state:
                    run.execution_state = outcome.execution_state
                    if outcome.answer:
                        run.partial_output = outcome.answer
                session.active_run_id = None
            elif run.status in {"stopped", "interrupted"}:
                if outcome.answer:
                    run.partial_output = outcome.answer
                    run.output = None
                    run.revision += 1
                run.completed_units = outcome.completed_units
                run.total_units = outcome.total_units
                run.current_unit = outcome.current_unit
                run.execution_state = outcome.execution_state
                session.active_run_id = None
            else:
                self._apply_outcome(session, run, outcome)
                session.active_run_id = None
            session.controls.pop(run_id, None)
            session.tasks.pop(run_id, None)
            await self._persist_locked(session)
            await self._dispatch_locked(session)

    def _apply_outcome(
        self, session: _SessionRuntime, run: RunEnvelope, outcome: RunResult
    ) -> None:
        if outcome.status == "completed":
            run.output = outcome.answer
            run.partial_output = None
        elif outcome.answer:
            run.partial_output = outcome.answer
            run.output = None
        run.error = outcome.error
        run.completed_units = outcome.completed_units
        run.total_units = outcome.total_units
        run.current_unit = outcome.current_unit
        run.execution_state = outcome.execution_state
        run.revision += 1
        if outcome.status in {"completed", "failed", "cancelled"}:
            run.completed_at = datetime.now(timezone.utc)
        if outcome.status == "completed":
            run.status = "completed"
            return
        if outcome.status == "failed":
            run.status = "failed"
            return
        if outcome.status == "cancelled":
            run.status = "interrupted"
            return
        if outcome.status == "needs_input":
            run.status = "waiting_user"
            questions = outcome.missing_inputs or [
                outcome.error.message if outcome.error is not None else "missing input"
            ]
            for question in questions:
                request = InputRequest(
                    session_id=run.session_id,
                    run_id=run.run_id,
                    question=question,
                )
                session.requests[request.request_id] = request
                run.pending_request_ids.append(request.request_id)
            return
        if outcome.status == "yielded":
            run.status = "queued"
            session.queue.appendleft(run.run_id)
            return
        run.status = "failed"
        run.error = Error(code="invalid_executor_status", message="invalid run status")

    async def _dispatch(self, session_id: str) -> None:
        async with self._lock:
            session = self._require_session(session_id)
            await self._dispatch_locked(session)

    async def _dispatch_locked(self, session: _SessionRuntime) -> None:
        if session.active_run_id is not None:
            return
        while session.queue:
            run_id = session.queue.popleft()
            run = session.runs[run_id]
            if run.status != "queued":
                continue
            run.status = "running"
            run.started_at = datetime.now(timezone.utc)
            control = RunControl()
            session.active_run_id = run_id
            session.controls[run_id] = control
            task = asyncio.create_task(
                self._execute(session.session_id, run_id, control)
            )
            session.tasks[run_id] = task
            await self._persist_locked(session)
            return

    async def _ensure_session(self, session_id: str) -> None:
        if self._store is None:
            async with self._lock:
                self._session(session_id)
            return
        restored = False
        async with self._lock:
            if session_id in self._sessions:
                return
            stored = await self._store.load(session_id=session_id)
            session = self._session(session_id)
            if stored is not None:
                self._restore_session(session, stored)
                restored = True
        if restored:
            async with self._lock:
                session = self._require_session(session_id)
                await self._persist_locked(session)
                await self._dispatch_locked(session)

    @staticmethod
    def _restore_session(session: _SessionRuntime, stored: PersistedSession) -> None:
        snapshot = stored.snapshot
        session.runs = {run.run_id: run.model_copy(deep=True) for run in snapshot.runs}
        session.requests = {
            request.request_id: request.model_copy(deep=True)
            for request in stored.input_requests
        }
        session.queue = deque(
            run_id
            for run_id in snapshot.queued_run_ids
            if run_id in session.runs and session.runs[run_id].status == "queued"
        )
        session.active_run_id = None
        for run in session.runs.values():
            if run.status == "running":
                if run.partial_output:
                    run.execution_state["resume_visible_output"] = run.partial_output
                run.status = "queued"
                run.error = None
                run.revision += 1
                if run.run_id not in session.queue:
                    session.queue.appendleft(run.run_id)

    async def _persist_locked(self, session: _SessionRuntime) -> None:
        if self.on_change:
            for run in session.runs.values():
                self.on_change(run)
        if self._store is None:
            return
        await self._store.save(
            PersistedSession(
                snapshot=SessionSnapshot(
                    session_id=session.session_id,
                    active_run_id=session.active_run_id,
                    queued_run_ids=list(session.queue),
                    waiting_run_ids=[
                        run_id
                        for run_id, run in session.runs.items()
                        if run.status == "waiting_user"
                    ],
                    runs=[run.model_copy(deep=True) for run in session.runs.values()],
                ),
                input_requests=[
                    request.model_copy(deep=True)
                    for request in session.requests.values()
                ],
            )
        )

    def _session(self, session_id: str) -> _SessionRuntime:
        return self._sessions.setdefault(session_id, _SessionRuntime(session_id))

    def _require_session(self, session_id: str) -> _SessionRuntime:
        session = self._sessions.get(session_id)
        if session is None:
            raise SchedulerCommandError("session not found")
        return session

    @staticmethod
    def _require_run(session: _SessionRuntime, run_id: str) -> RunEnvelope:
        run = session.runs.get(run_id)
        if run is None:
            raise SchedulerCommandError("run not found")
        return run

    def _result(self, session_id: str, run_id: str) -> DispatchResult:
        session = self._require_session(session_id)
        return self._result_locked(session, self._require_run(session, run_id))

    @staticmethod
    def _result_locked(session: _SessionRuntime, run: RunEnvelope) -> DispatchResult:
        return DispatchResult(
            session_id=session.session_id,
            run_id=run.run_id,
            status=run.status,
            input_request_ids=list(run.pending_request_ids),
        )
