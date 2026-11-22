"""In-memory Harness scheduler for the first lifecycle vertical slice.

The scheduler owns Session/Run routing. The existing Harness remains the
executor for one Run; persistence and parallel workers are intentionally left
behind a later port.
"""

import asyncio
import logging
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend.application.workspace.task_graph import validate_graph_capabilities
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
from backend.contracts.runtime import (
    GraphProgress,
    NodeState,
    RunGraph,
    RunPolicy,
    RunSpec,
)
from backend.contracts.schemas.core import Error
from backend.harness.graph.executor import validate_graph
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
        # Shutdown cancels worker tasks while preserving their latest checkpoint.
        async with self._lock:
            tasks = [task for session in self._sessions.values() for task in session.tasks.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def continue_run(
        self, *, session_id: str, run_id: str, guidance: str | None = None,
        payload: Any | None = None,
    ) -> DispatchResult:
        await self._ensure_session(session_id)
        async with self._lock:
            session = self._require_session(session_id)
            run = self._require_run(session, run_id)
            if run.status in {"running", "queued"}:
                return self._result_locked(session, run)
            if run.status not in {"paused", "failed"}:
                raise SchedulerCommandError("run cannot be continued")
            recovery = recovery_for_run(run)
            if not recovery.can_continue:
                raise SchedulerCommandError("run cannot be continued")
            still_stopping = run_id in session.tasks
            error_code = run.error.code if run.error else None
            prepared_state = prepare_checkpoint_for_continue(
                run.execution_state, error_code=error_code
            )
            existing_guidance = prepared_state.get("continuation_guidance", [])
            if not isinstance(existing_guidance, list) or any(
                not isinstance(item, str) for item in existing_guidance
            ):
                raise SchedulerCommandError("run continuation guidance is invalid")
            run.status, run.error, run.completed_at = "queued", None, None
            run.execution_state = prepared_state
            run.continuation_guidance = existing_guidance[-8:]
            if payload is not None:
                run.payload = payload
            instruction = guidance.strip() if guidance is not None else ""
            if instruction:
                prior_guidance = list(run.continuation_guidance)
                if not prior_guidance or prior_guidance[-1] != instruction:
                    prior_guidance.append(instruction)
                run.execution_state["continuation_guidance"] = prior_guidance[-8:]
                run.continuation_guidance = prior_guidance[-8:]
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
        graph: RunGraph | None = None,
        runtime_policy: RunPolicy | None = None,
    ) -> DispatchResult:
        if graph is not None:
            validate_graph_capabilities(graph, RunInput.model_validate(payload))
            validate_graph(graph)
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
                graph=graph,
                graph_id=graph.graph_id if graph else None,
                graph_version=graph.version if graph else None,
                spec=RunSpec(goal=payload.text, graph=graph, policy=runtime_policy or RunPolicy()) if graph else None,
                node_states={node.id: NodeState(node_id=node.id, kind=node.kind, label=node.label, input_refs=node.input_refs) for node in graph.nodes} if graph else {},
                runtime_policy=runtime_policy or RunPolicy(),
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
                if run.status != "succeeded":
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
            if request.kind == "mode_upgrade" and payload is None:
                raise SchedulerCommandError("mode choice requires the workbench resume command")
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
            control.cancel("steer")
            await self._persist_locked(session)
            return self._result_locked(session, run)

    async def pause(self, *, session_id: str, run_id: str) -> DispatchResult:
        return await self._cancel(
            session_id=session_id, run_id=run_id, terminal="paused"
        )

    async def cancel(self, *, session_id: str, run_id: str) -> DispatchResult:
        return await self._cancel(
            session_id=session_id, run_id=run_id, terminal="cancelled"
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
            if run.status in {"succeeded", "failed", "cancelled", "paused", "pausing"}:
                return self._result_locked(session, run)
            run.status = terminal if terminal == "cancelled" else "pausing"
            if terminal == "cancelled":
                run.completed_at = datetime.now(timezone.utc)
            run.revision += 1
            if run_id in session.queue:
                session.queue.remove(run_id)
            control = session.controls.get(run_id)
            if control is not None:
                control.cancel("cancel" if terminal == "cancelled" else "pause")
            elif terminal == "paused":
                run.status = "paused"
                run.paused_at = datetime.now(timezone.utc)
            should_dispatch = session.active_run_id != run_id
            await self._persist_locked(session)
        if should_dispatch:
            await self._dispatch(session_id)
        return self._result(session_id, run_id)

    async def _execute(self, session_id: str, run_id: str, control: RunControl) -> None:
        async with self._lock:
            session = self._require_session(session_id)
            run = self._require_run(session, run_id)
            elapsed_before_execution = run.active_elapsed_ms
            execution_input = run.model_copy(deep=True)

        async def checkpoint(state):
            async with self._lock:
                current = self._require_run(session, run_id)
                if session.controls.get(run_id) is not control:
                    return
                current.execution_state = state
                current.context_compaction_count = state.get("context_compaction_count", 0)
                await self._persist_locked(session)

        control.checkpoint = checkpoint

        async def graph_progress(progress: GraphProgress):
            async with self._lock:
                current = self._require_run(session, run_id)
                if session.controls.get(run_id) is not control:
                    return
                current.node_states = progress.node_states
                current.attempts = progress.attempts
                current.checkpoint = progress.checkpoint
                current.graph_artifacts = progress.artifacts
                current.artifact_refs = [artifact.artifact_id for artifact in progress.artifacts]
                current.current_node_id = progress.current_node_id
                current.current_attempt_id = progress.current_attempt_id
                current.execution_state = progress.state
                current.active_elapsed_ms = elapsed_before_execution + progress.elapsed_ms
                current.wall_elapsed_ms = int((datetime.now(timezone.utc) - current.created_at).total_seconds() * 1000)
                current.revision += 1
                await self._persist_locked(session)

        control.graph_progress = graph_progress
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
        except Exception as exc:
            logging.getLogger(__name__).error("executor failed run=%s type=%s stack=%s",
                run_id, type(exc).__name__, traceback.format_tb(exc.__traceback__))
            outcome = RunResult(
                run_id=run_id,
                status="failed",
                error=Error(code="executor_failed", message=f"run executor failed: {type(exc).__name__}"),
                execution_state=dict(run.execution_state),
                elapsed_seconds=0,
            )

        async with self._lock:
            session = self._require_session(session_id)
            run = self._require_run(session, run_id)
            # Pause/cancel settle the current execution even if an earlier
            # steer changed its input. Public projection revisions are not an
            # input generation: progress and control actions also change them.
            if run.status in {"cancelled", "pausing"}:
                if outcome.answer:
                    run.partial_output = outcome.answer
                    run.output = None
                    run.revision += 1
                run.completed_units = outcome.completed_units
                run.total_units = outcome.total_units
                run.current_unit = outcome.current_unit
                run.execution_state = outcome.execution_state
                run.context_compaction_count = outcome.execution_state.get("context_compaction_count", 0)
                if run.status == "pausing":
                    run.status = "paused"
                    run.paused_at = datetime.now(timezone.utc)
            elif control.reason == "steer":
                if outcome.execution_state:
                    run.execution_state = outcome.execution_state
                # Only a replaced input discards its result and queues the
                # same Run ahead of ordinary work, regardless of executor kind.
                run.status = "queued"
                session.queue.appendleft(run_id)
            else:
                self._apply_outcome(session, run, outcome)
            run.active_elapsed_ms = max(
                run.active_elapsed_ms,
                elapsed_before_execution + int(outcome.elapsed_seconds * 1000),
            )
            run.wall_elapsed_ms = int((datetime.now(timezone.utc) - run.created_at).total_seconds() * 1000)
            session.active_run_id = None
            session.controls.pop(run_id, None)
            session.tasks.pop(run_id, None)
            await self._persist_locked(session)
            await self._dispatch_locked(session)

    def _apply_outcome(
        self, session: _SessionRuntime, run: RunEnvelope, outcome: RunResult
    ) -> None:
        if outcome.status == "succeeded":
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
        run.context_compaction_count = outcome.execution_state.get("context_compaction_count", 0)
        run.revision += 1
        if outcome.status in {"succeeded", "failed", "cancelled"}:
            run.completed_at = datetime.now(timezone.utc)
        if outcome.status == "succeeded":
            run.status = "succeeded"
            return
        if outcome.status == "failed":
            run.status = "failed"
            return
        if outcome.status == "cancelled":
            run.status = "cancelled"
            return
        if outcome.status == "waiting_user":
            run.status = "waiting_user"
            if outcome.input_requests:
                for spec in outcome.input_requests:
                    request = InputRequest(
                        session_id=run.session_id,
                        run_id=run.run_id,
                        **spec,
                    )
                    session.requests[request.request_id] = request
                    run.pending_request_ids.append(request.request_id)
                return
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
            run.started_at = run.started_at or datetime.now(timezone.utc)
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
            if not run.continuation_guidance:
                saved_guidance = run.execution_state.get("continuation_guidance", [])
                if isinstance(saved_guidance, list) and all(
                    isinstance(item, str) for item in saved_guidance
                ):
                    run.continuation_guidance = saved_guidance[-8:]
            if run.status == "pausing":
                # The old worker no longer exists after restart. Honor its
                # accepted pause at the latest saved checkpoint, without
                # automatically dispatching more work.
                run.status = "paused"
                run.paused_at = datetime.now(timezone.utc)
                run.revision += 1
            if run.status == "running":
                if run.partial_output:
                    run.execution_state["resume_visible_output"] = run.partial_output
                run.status = "queued"
                run.error = None
                run.revision += 1
                if run.run_id not in session.queue:
                    session.queue.appendleft(run.run_id)

    async def _persist_locked(self, session: _SessionRuntime) -> None:
        if self._store is not None:
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
        if self.on_change:
            for run in session.runs.values():
                self.on_change(run)

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
