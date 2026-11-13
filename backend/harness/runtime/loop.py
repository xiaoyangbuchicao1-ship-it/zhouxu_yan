import asyncio
import hashlib
import json
from time import perf_counter
from uuid import uuid4

from backend.config.settings import Settings
from backend.contracts.events.runtime import RunEvent
from backend.contracts.events.streaming import StreamEvent, visible_text
from backend.contracts.harness.execution import ExecutionState
from backend.contracts.modules.loop import (
    ActionRequest,
    ActionResult,
    PerceptionInput,
    ReasoningInput,
    ReflectionInput,
    RunInput,
    RunResult,
    ToolSpec,
)
from backend.contracts.modules.memory import (
    MemoryInput,
    MemoryRecord,
    TaskState,
    TaskStep,
)
from backend.contracts.modules.perception import ContextItem
from backend.contracts.ports import EventSink, ModelContinuationPort, Module, StreamSink
from backend.contracts.schemas.core import (
    Error,
    ModuleRequest,
    ModuleResponse,
    PortError,
)
from backend.harness.lifecycle.control import RunControl, StopRequested
from backend.infrastructure.sources import source_refs_from_action


class Halt(Exception):
    def __init__(self, status: str, error: Error):
        self.status, self.error = status, error


class Harness:
    def __init__(
        self,
        *,
        perception: Module,
        memory: Module,
        reasoning: Module,
        action: Module,
        reflection: Module,
        settings: Settings,
        tools: list[ToolSpec],
        events: EventSink,
        streams: StreamSink | None = None,
        material_catalog=None,
        material_tool=None,
        material_search_tool=None,
        model_context=None,
        context_recovery=None,
        execution_tools=None,
    ):
        self.modules = dict(
            perception=perception,
            memory=memory,
            reasoning=reasoning,
            action=action,
            reflection=reflection,
        )
        self.settings, self.tools, self.events = settings, list(tools), events
        self.streams = streams
        self.material_catalog = material_catalog
        self.material_tool = material_tool
        self.material_search_tool = material_search_tool
        self.model_context = model_context
        self.context_recovery = context_recovery
        self.execution_tools = execution_tools

    def _emit(self, event: RunEvent):
        try:
            self.events.emit(event)
        except Exception:
            pass  # Optional telemetry must not turn a successful operation into failure.

    async def run(
        self,
        value: RunInput,
        control: RunControl | None = None,
        *,
        session_id: str | None = None,
        account_id: str | None = None,
        conversation_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        recovery: dict | None = None,
        history: list[MemoryRecord] | None = None,
        checkpoint=None,
    ) -> RunResult:
        run_id = run_id or uuid4().hex
        explicit_session = session_id is not None
        session_id = session_id or f"ephemeral:{run_id}"
        task_id = task_id or run_id
        control = control or RunControl()
        started = perf_counter()
        deadline = started + self.settings.runtime.run_timeout_seconds
        retries = 0
        saved = ExecutionState.model_validate(recovery or {})
        tool_counts: dict[str, int] = dict(saved.tool_counts)
        last_progress_stage: str | None = None
        completed_units = 0
        current_unit: str | None = None
        last_answer: str | None = None
        result_fingerprints: dict[str, int] = {}
        previous_results: dict[str, ActionResult] = {}
        stagnant_rounds = 0
        retained_results: list[dict] = list(saved.tool_results)
        retained_records: list[dict] = list(saved.records)
        work = saved.task_state or TaskState(task_id=task_id, session_id=session_id)
        compacted_ids = set(saved.compacted_record_ids)
        delivered_stages = list(saved.delivered_stages)
        remaining_work = list(saved.remaining_work)
        source_refs: list[dict] = list(saved.source_refs)
        recovery_mode = saved.recovery_mode
        compaction_requested = saved.compaction_requested
        active_skills: list[dict[str, str]] = [
            item
            for item in saved.active_skills
            if isinstance(item, dict)
            and isinstance(item.get("skill_id"), str)
            and isinstance(item.get("version"), str)
        ]
        # Older checkpoints did not have an explicit active_skills field.  A
        # read_skill receipt is sufficient to rebuild the small projection;
        # this never replays the tool itself.
        for raw in retained_results:
            if not isinstance(raw, dict) or raw.get("tool_name") != "read_skill":
                continue
            value = raw.get("result")
            if not isinstance(value, dict):
                continue
            skill_id = value.get("skill_id")
            version = value.get("version")
            if isinstance(skill_id, str) and isinstance(version, str):
                active_skills = [
                    item for item in active_skills if item.get("skill_id") != skill_id
                ]
                active_skills.append({"skill_id": skill_id, "version": version})
        continuation = self.model_context if isinstance(self.model_context, ModelContinuationPort) else None

        def delivered_prefix():
            return "\n\n".join(delivered_stages) + ("\n\n" if delivered_stages else "")

        def request_key(action):
            return json.dumps(
                [action.tool_name, action.arguments], sort_keys=True, ensure_ascii=False
            )

        def fingerprint(action, result):
            return hashlib.sha256(
                json.dumps(
                    [
                        request_key(action),
                        result.outcome,
                        result.result,
                        result.error.model_dump() if result.error else None,
                    ],
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()

        def note_result(action, result):
            signature = fingerprint(action, result)
            result_fingerprints[signature] = result_fingerprints.get(signature, 0) + 1
            previous_results[request_key(action)] = result
            return result_fingerprints[signature]

        # Rebuild only from actual persisted receipts, not model statements.
        saved_requests = {}
        for record in saved.records:
            body = record.get("body") or record.get("updates") or {}
            for action in body.get("proposal", {}).get("action_requests", []):
                saved_requests[action["action_id"]] = ActionRequest.model_validate(
                    action
                )
            receipt = body.get("action")
            if receipt and receipt["action_id"] in saved_requests:
                result = ActionResult.model_validate(receipt)
                if result.error is None or result.error.code != "tool_repeated":
                    note_result(saved_requests[result.action_id], result)

        def execution_state(reason=None):
            return ExecutionState(
                records=retained_records,
                tool_results=retained_results,
                task_state=work,
                tool_counts=tool_counts,
                compacted_record_ids=sorted(compacted_ids),
                rounds_completed=completed_units,
                material_coverage=self.material_catalog.coverage_for(run_id)
                if self.material_catalog
                else [],
                stop_reason=reason,
                context_resources=self.material_catalog.context_resources_for(run_id)
                if self.material_catalog
                else [],
                delivered_stages=delivered_stages,
                remaining_work=remaining_work,
                material_statuses={key: parsed.manifest.status for key, parsed in self.material_catalog.runs.get(run_id, {}).items()}
                if self.material_catalog else {},
                recovery_mode=recovery_mode,
                recovery_attempts=saved.recovery_attempts,
                compaction_requested=compaction_requested,
                source_refs=source_refs,
                active_skills=active_skills,
                model_continuation=continuation.export_run_state(run_id) if continuation else {},
            ).model_dump(mode="json")

        async def save_checkpoint():
            if checkpoint is not None:
                await checkpoint(execution_state())

        def guard():
            if control.cancelled:
                raise Halt("cancelled", self.settings.error("cancelled"))
            if perf_counter() >= deadline:
                raise Halt("failed", self.settings.error("timeout"))

        def progress(stage: str) -> None:
            nonlocal last_progress_stage, current_unit
            if (
                self.streams is None
                or not self.settings.progress.enabled
                or stage == last_progress_stage
            ):
                return
            last_progress_stage = stage
            current_unit = stage
            self.streams.emit_stream(
                StreamEvent(
                    type="progress",
                    run_id=run_id,
                    stage=stage,
                    label=self.settings.progress.labels.get(stage, stage),
                )
            )

        async def invoke(
            name, payload, *, context=None, durable=False, read_only=False
        ):
            nonlocal retries
            while True:
                if not durable:
                    guard()
                if name != "action" and (
                    name != "memory" or payload.operation == "recall"
                ):
                    progress(name)
                call_id = uuid4().hex
                began = perf_counter()
                self._emit(
                    RunEvent(
                        run_id=run_id, call_id=call_id, module=name, phase="started"
                    )
                )
                try:
                    operation = self.modules[name].handle(
                        ModuleRequest(
                            run_id=run_id,
                            call_id=call_id,
                            input=payload,
                            context=context,
                        )
                    )
                    module_timeout = self.settings.runtime.call_timeout_seconds
                    if name == "reasoning":
                        module_timeout = self.settings.runtime.model_call_timeout_seconds or module_timeout
                        module_timeout = min(module_timeout, self.settings.model.generation_mode.call_timeout_seconds)
                    elif name == "action":
                        module_timeout = self.settings.runtime.action_timeout_seconds or module_timeout
                    response = await asyncio.wait_for(
                        control.read(operation) if name == "reasoning" else operation,
                        timeout=min(
                            module_timeout,
                            self.settings.runtime.call_timeout_seconds
                            if durable
                            else deadline - perf_counter(),
                        ),
                    )
                    if not isinstance(response, ModuleResponse):
                        response = ModuleResponse(
                            status="failed", error=self.settings.error("missing_output")
                        )
                    if response.status == "ok" and response.output is None:
                        response = ModuleResponse(
                            status="failed", error=self.settings.error("missing_output")
                        )
                except TimeoutError:
                    error = self.settings.error("timeout")
                    if (
                        name == "action"
                        and read_only
                        and error.code
                        in self.settings.runtime.read_only_recoverable_errors
                    ):
                        response = ModuleResponse(
                            status="ok",
                            output=ActionResult(
                                tool_name=payload.tool_name,
                                action_id=payload.action_id,
                                outcome="error",
                                error=error,
                                result={
                                    "status": "failed",
                                    "error": error.code,
                                    "message": error.message,
                                },
                                session_summary={
                                    "status": "failed",
                                    "error": error.code,
                                },
                            ),
                        )
                    else:
                        response = ModuleResponse(status="failed", error=error)
                except StopRequested:
                    self._emit(
                        RunEvent(
                            run_id=run_id,
                            call_id=call_id,
                            module=name,
                            phase="finished",
                            status="cancelled",
                            elapsed_seconds=perf_counter() - began,
                        )
                    )
                    raise Halt("cancelled", self.settings.error("cancelled"))
                except asyncio.CancelledError:
                    self._emit(
                        RunEvent(
                            run_id=run_id,
                            call_id=call_id,
                            module=name,
                            phase="finished",
                            status="cancelled",
                        )
                    )
                    raise
                except Exception:
                    response = ModuleResponse(
                        status="failed", error=self.settings.error("module_failed")
                    )
                self._emit(
                    RunEvent(
                        run_id=run_id,
                        call_id=call_id,
                        module=name,
                        phase="finished",
                        status=(
                            "failed"
                            if name == "action"
                            and response.output is not None
                            and response.output.outcome == "error"
                            else response.status
                        ),
                        elapsed_seconds=perf_counter() - began,
                        error_code=(
                            response.error.code
                            if response.error
                            else response.output.error.code
                            if name == "action"
                            and response.output is not None
                            and response.output.error
                            else None
                        ),
                        diagnostics=response.output.assembly.model_dump(mode="json")
                        if name == "perception" and response.output is not None
                        else None,
                    )
                )
                if name != "action" and not durable:
                    guard()
                if response.status == "ok":
                    return response.output
                error = response.error or self.settings.error("missing_output")
                # No automatic replay of tools/writes. Only model decisions may be retried.
                if (
                    name == "reasoning"
                    and response.status == "failed"
                    and error.retryable
                ):
                    if retries >= self.settings.runtime.max_retries:
                        # Preserve the real cause (e.g. provider unavailable),
                        # not an implementation-budget error in its place.
                        raise Halt("failed", error)
                    retries += 1
                    self._emit(RunEvent(run_id=run_id, module=name, phase="retrying"))
                    continue
                raise Halt(response.status, error)

        async def remember(source, updates, summary="", *, retention=None):
            record = MemoryRecord(
                session_id=session_id,
                run_id=run_id,
                task_id=task_id,
                source=source,
                kind={
                    "user": "interaction",
                    "reasoning": "decision",
                    "action": "tool_reference",
                    "reflection": "progress_update",
                }.get(source, "interaction"),
                summary=summary,
                body=updates,
                retention=retention or ("session" if explicit_session else "run"),
            )
            result = await invoke(
                "memory",
                MemoryInput(
                    operation="record",
                    items=[record],
                    session_id=session_id,
                    task_id=task_id,
                ),
                durable=True,
            )
            retained_records.append(record.model_dump(mode="json"))
            return result

        def finish(status, *, answer=None, error=None, missing_inputs=None):
            answer = answer or last_answer
            if not answer and status != "completed":
                answer = (
                    visible_text(
                        event
                        for event in getattr(self.streams, "stream_events", [])
                        if event.run_id == run_id
                    )
                    or None
                )
            if status == "completed":
                progress("finalizing")
                if self.streams is not None:
                    self.streams.emit_stream(
                        StreamEvent(type="done", run_id=run_id, text=answer or "")
                    )
            elif status == "needs_input":
                progress("waiting_user")
            elif status == "yielded":
                progress("stage_delivered")
            elif self.streams is not None:
                self.streams.emit_stream(
                    StreamEvent(type="error", run_id=run_id, error=error)
                )
            result = RunResult(
                run_id=run_id,
                status=status,
                answer=answer,
                error=error,
                missing_inputs=missing_inputs or [],
                elapsed_seconds=perf_counter() - started,
                # Rounds are a resource ceiling, not measured task completion.
                completed_units=None,
                total_units=None,
                current_unit=current_unit,
                execution_state=execution_state(error.code if error else status),
            )
            self._emit(
                RunEvent(
                    run_id=run_id,
                    module="harness",
                    phase="finished",
                    status=status,
                    elapsed_seconds=result.elapsed_seconds,
                    error_code=error.code if error else None,
                )
            )
            return result

        self._emit(RunEvent(run_id=run_id, module="harness", phase="started"))
        try:
            if continuation:
                continuation.restore_run_state(run_id, saved.model_continuation)
            material_candidates, required_material_ids = ([], [])
            if self.material_catalog and value.materials:
                progress("material_parsing")
                material_candidates, required_material_ids = await asyncio.wait_for(
                    self.material_catalog.ingest(value.materials, run_id=run_id),
                    timeout=max(0.001, deadline - perf_counter()),
                )
            if self.material_catalog:
                # Do not turn an arbitrary character count into a model limit.
                # Only the complete encoded request can trigger externalization.
                original_item = ContextItem(
                    id="current-user",
                    type="message",
                    source="user",
                    role="user",
                    body={
                        "goal": value.text,
                        "facts": value.facts,
                        "constraints": value.constraints,
                    },
                )
                initial_tools = list(self.tools) + [
                    tool.spec
                    for tool in (self.material_tool, self.material_search_tool)
                    if tool
                ]
                over = (
                    self.model_context.estimate_context(
                        [original_item], initial_tools, run_id=run_id
                    ).tokens
                    > self.model_context.context_budget().warning
                )
                value = self.material_catalog.prepare_user_input(
                    value,
                    run_id=run_id,
                    over_budget=over,
                    preview_chars=min(
                        self.settings.context_policy.material_excerpt_chars,
                        self.model_context.context_budget().low
                        // self.settings.context_policy.tool_char_token_bound,
                    ),
                )
                material_candidates = self.material_catalog.candidates(
                    run_id, task_text=value.text
                )
            # Chat is deliberately read-only.  Work keeps the same research
            # tools and may additionally receive execution tools as they are
            # registered.  The allow-list is enforced again at dispatch so a
            # model cannot bypass the advertised schemas with a guessed name.
            mode_allowed_names = {
                tool.name
                for tool in self.tools
                if value.mode is None or value.mode == "work" or tool.read_only
            }
            registered_specs = {tool.name: tool for tool in self.tools}
            if self.material_tool:
                registered_specs[self.material_tool.spec.name] = self.material_tool.spec
                mode_allowed_names.add(self.material_tool.spec.name)
                if self.material_search_tool:
                    registered_specs[self.material_search_tool.spec.name] = self.material_search_tool.spec
                    mode_allowed_names.add(self.material_search_tool.spec.name)
            available_tools = [tool for tool in self.tools if tool.name in mode_allowed_names]
            for raw in retained_results:
                if self.material_catalog:
                    self.material_catalog.bind_result(
                        ActionResult.model_validate(raw), run_id=run_id
                    )
            if self.material_catalog:
                for resource in saved.context_resources:
                    self.material_catalog.bind_text(
                        resource["material_id"],
                        resource["name"],
                        resource["text"],
                        run_id=run_id,
                    )
            if retained_records:
                # Rehydrate prior results, never replay their tool side effects.
                await invoke(
                    "memory",
                    MemoryInput(
                        operation="record",
                        session_id=session_id,
                        task_id=task_id,
                        items=[
                            MemoryRecord.model_validate(r) for r in retained_records
                        ],
                    ),
                )
            if history:
                existing = await invoke(
                    "memory",
                    MemoryInput(
                        operation="recall", session_id=session_id, task_id=task_id
                    ),
                )
                known_runs = {r.run_id for r in existing.records}
                restored = [r for r in history if r.run_id not in known_runs]
                if restored:
                    await invoke(
                        "memory",
                        MemoryInput(
                            operation="restore",
                            session_id=session_id,
                            task_id=task_id,
                            items=restored,
                        ),
                    )
            await remember("user", value.model_dump(), value.text.strip())
            # Crash/restart may leave issued calls without a receipt. Never
            # replay them: their external side effect is unknown.
            known_results = {r["action_id"] for r in retained_results}
            for raw in list(retained_records):
                for pending in (
                    raw.get("body", {}).get("proposal", {}).get("action_requests", [])
                ):
                    if pending["action_id"] not in known_results:
                        receipt = ActionResult(
                            tool_name=pending["tool_name"],
                            action_id=pending["action_id"],
                            result={
                                "status": "unavailable",
                                "error": "execution_outcome_unknown",
                                "message": "上次运行未保存此调用结果；未自动重放操作。",
                            },
                        )
                        retained_results.append(receipt.model_dump(mode="json"))
                        await remember(
                            "action", {"action": receipt.model_dump()}, retention="run"
                        )
                        known_results.add(receipt.action_id)
            finalizing = False
            for round_index in range(self.settings.runtime.max_rounds):
                current_unit = f"round:{round_index + 1}"
                if self.material_catalog:
                    material_candidates = self.material_catalog.candidates(run_id)
                memory = await invoke(
                    "memory",
                    MemoryInput(
                        operation="recall",
                        session_id=session_id,
                        task_id=task_id,
                    ),
                )
                # A committed summary covers only its declared records. Task
                # progress and current requirements retain separate identities.
                memory.records = [
                    r for r in memory.records if r.record_id not in compacted_ids
                ]
                memory.items = memory.records
                memory.required_item_ids = [
                    key for key in memory.required_item_ids if key not in compacted_ids
                ]
                if memory.task_state and not (work.steps or work.blockers or work.goal):
                    work = memory.task_state.model_copy(deep=True)
                memory.task_state = (
                    work if work.steps or work.blockers or work.goal else None
                )
                compactions = 0
                loop_candidates = list(material_candidates)
                loop_required = list(required_material_ids)
                if delivered_stages:
                    prefix = "\n\n".join(delivered_stages)
                    resource_id = "context:delivered:" + run_id
                    excerpt_chars = self.settings.context_policy.material_excerpt_chars
                    if self.material_catalog and len(prefix) > excerpt_chars:
                        self.material_catalog.bind_text(resource_id, "本任务已交付正文", prefix, run_id=run_id)
                    loop_candidates.append(ContextItem(
                        id="task-continuation-state", type="message", source="harness", role="user", kind="delivery_state",
                        body={"already_delivered_chars": len(prefix), "tail_excerpt": prefix[-excerpt_chars:],
                              "excerpt_only": len(prefix) > excerpt_chars,
                              "readable_material_id": resource_id if len(prefix) > excerpt_chars and self.material_catalog else None,
                              "remaining_work": remaining_work,
                              "instruction": self.settings.prompts["continue_visible"]},
                    ))
                    loop_required.append("task-continuation-state")
                if active_skills:
                    loop_candidates.append(
                        ContextItem(
                            id="active-skill-state",
                            type="message",
                            source="harness",
                            role="user",
                            kind="skill.activation",
                            body={
                                "skills": active_skills,
                                "instruction": "已加载的 Skill 版本记录；需要完整方法正文时使用 read_skill 重新读取。",
                            },
                        )
                    )
                    loop_required.append("active-skill-state")
                for attempt in range(self.settings.context_policy.max_compactions + 2):
                    perceived = await invoke(
                        "perception",
                        PerceptionInput(
                            current=value,
                            memory=memory,
                            candidates=loop_candidates,
                            required_candidate_ids=loop_required,
                            tools=available_tools,
                        ),
                    )
                    if self.material_catalog and self.material_catalog.runs.get(run_id):
                        missing_tools = [
                            tool.spec
                            for tool in (self.material_tool, self.material_search_tool)
                            if tool
                            and not any(
                                t.name == tool.spec.name for t in available_tools
                            )
                        ]
                        if missing_tools:
                            available_tools.extend(missing_tools)
                            continue  # Include both schemas in the complete request budget.
                    if not perceived.needs_compaction and not (
                        compaction_requested and attempt == 0
                    ):
                        break
                    if (
                        compactions >= self.settings.context_policy.max_compactions
                        or self.context_recovery is None
                    ):
                        raise Halt(
                            "failed", self.settings.error("context_recovery_exhausted")
                        )
                    compactions += 1
                    summary, covered = await asyncio.wait_for(
                        control.read(
                            self.context_recovery.summarize(perceived, run_id=run_id)
                        ),
                        timeout=min(
                            self.settings.context_policy.compaction_seconds,
                            deadline - perf_counter(),
                        ),
                    )
                    result = await remember(
                        "memory",
                        {"text": summary, "covered_record_ids": covered},
                        retention="run",
                    )
                    compacted_ids.update(covered)
                    await save_checkpoint()
                    memory = result
                    memory.records = [
                        r for r in memory.records if r.record_id not in compacted_ids
                    ]
                    memory.items = memory.records
                    memory.required_item_ids = [
                        key
                        for key in memory.required_item_ids
                        if key not in compacted_ids
                    ]
                    memory.task_state = (
                        work if work.steps or work.blockers or work.goal else None
                    )
                    compaction_requested = False
                    recovery_mode = None
                if self.material_catalog:
                    self.material_catalog.record_selected(
                        perceived.items, run_id=run_id
                    )
                proposal = await invoke(
                    "reasoning",
                    ReasoningInput(
                        perceived=perceived,
                        tools=available_tools,
                        delivered_prefix=delivered_prefix(),
                        purpose="finalization"
                        if finalizing
                        or (
                            round_index > 0
                            and round_index == self.settings.runtime.max_rounds - 1
                        )
                        else "main",
                    ),
                )
                if (
                    finalizing
                    or (
                        round_index > 0
                        and round_index == self.settings.runtime.max_rounds - 1
                    )
                ) and proposal.kind == "action":
                    raise Halt(
                        "failed",
                        self.settings.error(
                            "tool_no_progress" if work.blockers else "round_limit"
                        ),
                    )
                if proposal.answer:
                    last_answer = delivered_prefix() + proposal.answer
                if proposal.incomplete:
                    await remember("reasoning", {"proposal": proposal.model_dump()})
                    return finish(
                        "failed",
                        answer=proposal.answer,
                        error=self.settings.error(
                            {
                                "length": "model_output_incomplete",
                                "invalid_response": "model_invalid_response",
                                "transport_error": "model_unavailable",
                            }.get(proposal.finish_reason, "model_generation_stopped")
                        ),
                    )
                if (
                    proposal.kind == "answer"
                    and self.material_catalog
                    and self.material_catalog.has_unread_mixed_input(run_id)
                ):
                    return finish(
                        "failed",
                        answer=proposal.answer,
                        error=self.settings.error("mixed_input_incomplete"),
                    )
                await remember("reasoning", {"proposal": proposal.model_dump()})
                await save_checkpoint()
                if proposal.kind == "answer" and proposal.blocked:
                    cause = next((ActionResult.model_validate(r).error for r in reversed(retained_results) if r.get("error")), None)
                    return finish("failed", answer=delivered_prefix() + proposal.answer, error=cause or self.settings.error("task_blocked"))
                if proposal.kind == "answer" and not proposal.complete:
                    if proposal.answer not in delivered_stages:
                        delivered_stages.append(proposal.answer)
                    else:
                        raise Halt("failed", self.settings.error("tool_no_progress"))
                    remaining_work = list(proposal.remaining_work)
                    last_answer = "\n\n".join(delivered_stages)
                    await remember(
                        "reflection",
                        {"delivered_stage": proposal.answer, "remaining_work": remaining_work},
                        self.settings.prompts["continue_stage"],
                        retention="run",
                    )
                    await save_checkpoint()
                    progress("stage_delivered")
                    continue
                actions = []
                if proposal.kind == "action":
                    repeated_actions = []
                    for action_request in proposal.action_requests:
                        name = action_request.tool_name
                        spec = next(
                            (t for t in available_tools if t.name == name), None
                        )
                        mode_denied = (
                            name in registered_specs and name not in mode_allowed_names
                        )
                        prior = previous_results.get(request_key(action_request))
                        reuse = bool(
                            spec and spec.read_only and spec.reuse_identical_results
                        )
                        blocked = bool(
                            reuse
                            and prior
                            and result_fingerprints.get(
                                fingerprint(action_request, prior), 0
                            )
                            >= self.settings.context_policy.repeated_tool_limit
                        )
                        progress(
                            {
                                "read_material": "material_reading",
                                "search_material": "material_searching",
                                "search_cases": "case_searching",
                                "read_case": "case_reading",
                            }.get(name, "action")
                        )
                        allocation = self.model_context.allocate_tool_context(
                            perceived.assembly.used_tokens,
                            len(proposal.action_requests),
                            sum(
                                bool(a.arguments.get("include_image"))
                                for a in proposal.action_requests
                            ),
                        )
                        read_chars = allocation["chars"]
                        if mode_denied:
                            error = self.settings.error("tool_not_available_in_mode")
                            result = ActionResult(
                                tool_name=name,
                                action_id=action_request.action_id,
                                outcome="error",
                                error=error,
                                result={
                                    "status": "not_executed",
                                    "message": error.message,
                                    "mode": value.mode or "legacy",
                                },
                                session_summary={
                                    "status": "not_executed",
                                    "error": error.code,
                                },
                            )
                            self._emit(
                                RunEvent(
                                    run_id=run_id,
                                    module="action",
                                    phase="finished",
                                    status="not_executed",
                                    error_code=error.code,
                                )
                            )
                        elif blocked:
                            error = Error(
                                code="tool_repeated",
                                message=self.settings.messages.get(
                                    "tool_repeated",
                                    self.settings.messages["tool_no_progress"],
                                ),
                            )
                            result = ActionResult(
                                tool_name=name,
                                action_id=action_request.action_id,
                                outcome="error",
                                error=error,
                                result={
                                    "status": "not_executed",
                                    "message": error.message,
                                    "previous_action_id": prior.action_id,
                                },
                                session_summary={
                                    "status": "not_executed",
                                    "error": error.code,
                                },
                            )
                            self._emit(
                                RunEvent(
                                    run_id=run_id,
                                    module="action",
                                    phase="finished",
                                    status="not_executed",
                                    error_code=error.code,
                                )
                            )
                        else:
                            tool_counts[name] = tool_counts.get(name, 0) + 1
                            result = await invoke(
                                "action",
                                action_request,
                                read_only=bool(spec and spec.read_only),
                                context={
                                    "tool_call_index": tool_counts[name],
                                    "user_request": value.text,
                                    "user_facts": value.facts,
                                    "user_constraints": value.constraints,
                                    "account_id": account_id,
                                    "conversation_id": conversation_id or session_id if explicit_session else None,
                                    "conversation_context": self._tool_history(
                                        perceived.items
                                    ),
                                    "material_read_chars": read_chars,
                                    "tool_result_chars": read_chars,
                                    "image_allowed": bool(
                                        self.model_context
                                        and self.model_context.supports_images()
                                    ),
                                },
                            )
                        actions.append(result)
                        if self.material_catalog:
                            if result.outcome != "error":
                                self.material_catalog.bind_result(result, run_id=run_id)
                        if result.tool_name == "read_skill" and result.outcome == "ok":
                            payload = result.result if isinstance(result.result, dict) else {}
                            skill_id = payload.get("skill_id")
                            version = payload.get("version")
                            if isinstance(skill_id, str) and isinstance(version, str):
                                active_skills = [
                                    item
                                    for item in active_skills
                                    if item.get("skill_id") != skill_id
                                ]
                                active_skills.append(
                                    {"skill_id": skill_id, "version": version}
                                )
                        retained_results.append(result.model_dump(mode="json"))
                        for ref in source_refs_from_action(result):
                            if ref["source_id"] not in {item.get("source_id") for item in source_refs}:
                                source_refs.append(ref)
                        await remember(
                            "action",
                            {"action": result.model_dump()},
                            retention="run"
                            if result.session_summary is not None
                            else None,
                        )
                        if explicit_session and result.session_summary is not None:
                            await remember(
                                "tool_reference",
                                {
                                    "tool_name": result.tool_name,
                                    "action_id": result.action_id,
                                    "summary": result.session_summary,
                                },
                            )
                        status = (
                            result.result.get("status", "ok")
                            if isinstance(result.result, dict)
                            else "ok"
                        )
                        work.steps = [
                            step
                            for step in work.steps
                            if step.step_id != result.action_id
                        ]
                        work.steps.append(
                            TaskStep(
                                step_id=result.action_id,
                                title=f"{name}: {action_request.arguments.get('query', '')}",
                                status="blocked"
                                if result.outcome == "error"
                                or status in {"failed", "unavailable"}
                                else "completed",
                                evidence_refs=[result.action_id],
                            )
                        )
                        await save_checkpoint()
                        count = None if blocked else note_result(action_request, result)
                        if blocked or (
                            reuse
                            and count
                            >= self.settings.context_policy.repeated_tool_limit
                        ):
                            repeated_actions.append(action_request.action_id)
                    if repeated_actions:
                        await remember(
                            "reflection",
                            {"repeated_actions": repeated_actions},
                            self.settings.prompts.get(
                                "tool_repetition",
                                self.settings.reflection["continue_feedback"],
                            ),
                            retention="run",
                        )
                    stagnant_rounds = (
                        stagnant_rounds + 1
                        if len(repeated_actions) == len(proposal.action_requests)
                        else 0
                    )
                    if stagnant_rounds >= self.settings.runtime.no_progress_rounds:
                        finalizing = True
                        work.blockers = [self.settings.messages["tool_no_progress"]]
                    limit = self.settings.rag_tool.max_calls_per_run
                    if (
                        limit
                        and tool_counts.get(self.settings.rag_tool.name, 0) >= limit
                    ):
                        available_tools = [
                            tool
                            for tool in available_tools
                            if tool.name != self.settings.rag_tool.name
                        ]
                    work.revision += 1
                    await save_checkpoint()
                review = await invoke(
                    "reflection", ReflectionInput(proposed=proposal, actions=actions)
                )
                completed_units = round_index + 1
                await remember(
                    "reflection", {"review": review.model_dump()}, review.feedback
                )
                await save_checkpoint()
                if review.decision == "complete":
                    if proposal.kind != "answer" or not (proposal.answer or "").strip():
                        raise Halt("failed", self.settings.error("invalid_completion"))
                    remaining_work = []
                    return finish("completed", answer=delivered_prefix() + proposal.answer)
                if review.decision == "needs_input":
                    questions = proposal.missing_inputs or review.issues
                    return finish(
                        "needs_input",
                        missing_inputs=questions,
                        error=self.settings.error("missing_input"),
                    )
                if review.decision == "fail":
                    raise Halt("failed", self.settings.error("reflection_failed"))
                if review.decision == "retry":
                    if retries >= self.settings.runtime.max_retries:
                        raise Halt("failed", self.settings.error("retry_limit"))
                    retries += 1
                    self._emit(
                        RunEvent(run_id=run_id, module="reflection", phase="retrying")
                    )
            if remaining_work and len(delivered_stages) > len(saved.delivered_stages):
                return finish("yielded", answer="\n\n".join(delivered_stages))
            return finish("failed", error=self.settings.error("round_limit"))
        except Halt as exc:
            return finish(exc.status, error=exc.error)
        except StopRequested:
            return finish("cancelled", error=self.settings.error("cancelled"))
        except PortError as exc:
            return finish("failed", error=exc.error)
        except TimeoutError:
            return finish("failed", error=self.settings.error("timeout"))
        except asyncio.CancelledError as exc:
            exc.run_result = finish("cancelled", error=self.settings.error("cancelled"))
            raise
        except Exception:
            return finish("failed", error=self.settings.error("module_failed"))
        finally:
            # In-memory, run-scoped memory is released on every exit, including cancellation.
            try:
                await asyncio.wait_for(
                    self.modules["memory"].handle(
                        ModuleRequest(
                            run_id=run_id,
                            call_id=uuid4().hex,
                            input=MemoryInput(
                                operation="close_run",
                                session_id=session_id,
                                task_id=task_id,
                                purge_session=not explicit_session,
                            ),
                        )
                    ),
                    timeout=self.settings.runtime.call_timeout_seconds,
                )
            except Exception:
                pass
            if self.material_catalog:
                self.material_catalog.close_run(run_id)
            if continuation:
                continuation.close_run(run_id)

    def _tool_history(self, items):
        """Only selected prior user messages; no evidence bodies or model conclusions."""
        cfg = self.settings.runtime
        selected, used = [], 0
        for item in reversed(items):
            if (
                item.id == "current-user"
                or item.source != "user"
                or item.status != "available"
            ):
                continue
            text = item.body.get("updates", {}).get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            if (
                len(selected) >= cfg.tool_history_items
                or used + len(text) > cfg.tool_history_chars
            ):
                break
            selected.append({"record_id": item.id, "text": text})
            used += len(text)
        return list(reversed(selected))
