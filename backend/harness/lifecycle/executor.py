"""Adapters from the lifecycle scheduler to the single-Run Harness."""

import asyncio
import inspect
from typing import Any

from backend.contracts.events.streaming import visible_text
from backend.contracts.harness.lifecycle import RunEnvelope
from backend.contracts.modules.loop import RunInput, RunResult
from backend.contracts.modules.memory import MemoryRecord
from backend.harness.lifecycle.control import RunControl
from backend.harness.runtime.loop import Harness


class HarnessRunExecutor:
    """Run the existing Harness while preserving scheduler correlation IDs."""

    def __init__(
        self,
        harness: Harness,
        *,
        answer_fact_key: str = "user_answers",
        material_service=None,
        history_service=None,
        harnesses: dict[str, Harness] | None = None,
        execution_tools=None,
    ):
        self._harness = harness
        self._harnesses = harnesses or {}
        self._answer_fact_key = answer_fact_key
        self._material_service = material_service
        self._history_service = history_service
        self._execution_tools = execution_tools or getattr(harness, "execution_tools", None)

    async def execute(self, run: RunEnvelope, control: RunControl) -> RunResult:
        value = RunInput.model_validate(run.payload)
        mode = value.thinking_mode or self._harness.settings.model.thinking_mode
        harness = self._harnesses.get(mode, self._harness)
        if self._harnesses and mode not in self._harnesses:
            raise ValueError("unsupported_thinking_mode")
        recovery = dict(run.execution_state)
        visible = recovery.pop("resume_visible_output", None)
        if visible:
            stages = list(recovery.get("delivered_stages", []))
            prefix = "\n\n".join(stages)
            draft = visible[len(prefix):].lstrip("\n") if prefix and visible.startswith(prefix) else visible
            if draft:
                stages.append(draft)
                recovery["delivered_stages"] = stages
                records = list(recovery.get("records", []))
                records.append(MemoryRecord(
                    session_id=run.session_id, run_id=run.run_id,
                    task_id=run.task_id or run.run_id, source="reflection",
                    kind="progress_update", retention="run",
                    summary=self._harness.settings.prompts["continue_visible"],
                    body={"already_visible_output": visible, "notice": "停止或进程结束前已显示的正文草稿，不代表整个任务完成。"},
                ).model_dump(mode="json"))
                recovery["records"] = records
        history = []
        if self._history_service and run.account_id and run.conversation_id:
            history = self._history_service.context_records(
                account_id=run.account_id,
                conversation_id=run.conversation_id,
                exclude_run_id=run.run_id,
            )
        if value.material_ids:
            if self._material_service is None:
                raise RuntimeError("material_service_unavailable")
            if run.account_id is None:
                raise RuntimeError("material_account_identity_required")
            resolve_kwargs = {
                "account_id": run.account_id,
                "session_id": run.session_id,
                "material_ids": value.material_ids,
            }
            if run.conversation_id is not None:
                resolve_kwargs["conversation_id"] = run.conversation_id
            materials = self._material_service.resolve(**resolve_kwargs)
            value = value.model_copy(
                update={"materials": materials, "material_ids": []}
            )
        if run.answers:
            facts: dict[str, Any] = dict(value.facts)
            facts[self._answer_fact_key] = dict(run.answers)
            value = value.model_copy(update={"facts": facts})
        material_states = {}

        async def checkpoint(state):
            set_status = getattr(self._material_service, "set_status", None)
            if set_status and run.account_id:
                for key, status in state.get("material_statuses", {}).items():
                    if key not in {m.material_id for m in value.materials} or material_states.get(key) == status:
                        continue
                    # Status reflects real extraction, not a front-end upload animation.
                    self._material_service.set_status(account_id=run.account_id, material_id=key, status="parsing")
                    self._material_service.set_status(account_id=run.account_id, material_id=key, status="ready" if status in {"available", "partial"} else "failed")
                    material_states[key] = status
            if control.checkpoint:
                await control.checkpoint(state)
        try:
            run_kwargs = {
                "session_id": run.session_id,
                "account_id": run.account_id,
                "conversation_id": run.conversation_id,
                "task_id": run.task_id,
                "run_id": run.run_id,
                "recovery": recovery,
                "history": history,
                "checkpoint": checkpoint,
            }
            # Keep lightweight test and external Harness adapters compatible
            # while the production Harness carries the account scope.
            parameters = inspect.signature(harness.run).parameters
            if not any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
                run_kwargs = {key: value for key, value in run_kwargs.items() if key in parameters}
            result = await harness.run(value, control, **run_kwargs)
            # The Harness stream is the source of visible text. When execution
            # stops or fails before producing a final answer, project its deltas
            # into the Run result so the scheduler can persist partial_output.
            if not result.answer and result.status != "completed":
                stream_events = getattr(harness.streams, "stream_events", [])
                partial = visible_text(
                    event for event in stream_events if event.run_id == run.run_id
                )
                if partial:
                    result = result.model_copy(update={"answer": partial})
            if self._execution_tools is not None:
                artifacts = await asyncio.to_thread(
                    self._execution_tools.collect_artifacts,
                    run.run_id,
                    completed=result.status == "completed",
                )
                if artifacts:
                    state = dict(result.execution_state or {})
                    state["artifacts"] = artifacts
                    result = result.model_copy(update={"execution_state": state})
            return result
        finally:
            if self._execution_tools is not None:
                await self._execution_tools.close_run(run.run_id)
