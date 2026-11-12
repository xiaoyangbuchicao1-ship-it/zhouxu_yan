"""One production assembly path: normalize, protect, select, measure."""

from time import perf_counter

from backend.contracts.events.streaming import StreamEvent
from backend.contracts.modules.loop import PerceivedContext
from backend.contracts.modules.perception import ContextAssembly
from backend.contracts.schemas.core import ModuleResponse, PortError
from backend.modules.perception.inventory import organize_context
from backend.modules.perception.normalize import normalize_context
from backend.modules.perception.selection import select_context


class TextPerception:
    def __init__(self, settings, model_context, streams=None):
        self.settings, self.model_context = settings, model_context
        self.streams = streams

    async def handle(self, request):
        value, memory = request.input.current, request.input.memory
        began = perf_counter()
        if memory.scope != request.run_id or (
            memory.run_id and memory.run_id != request.run_id
        ):
            return ModuleResponse(
                status="failed", error=self.settings.error("memory_scope")
            )
        if memory.blocked_reason == "required_context_unavailable":
            return ModuleResponse(
                status="failed",
                error=self.settings.error("required_context_unavailable"),
            )
        records = memory.records or memory.items
        if any(
            r.session_id is not None and r.session_id != memory.session_id
            for r in records
        ):
            return ModuleResponse(
                status="failed", error=self.settings.error("memory_scope")
            )
        if memory.task_state and (
            memory.task_state.session_id != memory.session_id
            or memory.task_state.task_id != memory.task_id
        ):
            return ModuleResponse(
                status="failed", error=self.settings.error("memory_scope")
            )
        if not value.text.strip():
            return ModuleResponse(
                status="needs_input", error=self.settings.error("missing_input")
            )
        try:
            items = normalize_context(
                value,
                records,
                request.input.candidates,
                run_id=request.run_id,
                task_state=memory.task_state,
            )
            aliases = {
                r.record_id
                for r in records
                if r.source == "user"
                and r.run_id == request.run_id
                and r.payload == value.model_dump()
            }
            required = (
                {"current-user"}
                | (set(memory.required_item_ids) - aliases)
                | set(request.input.required_candidate_ids)
            )
            if memory.task_state:
                required.add(f"task-state:{memory.task_state.task_id}")
            latest_call = next(
                (item for item in reversed(items) if item.type == "tool_call"), None
            )
            if latest_call:
                required.add(latest_call.id)
            if required - {i.id for i in items}:
                raise ValueError("missing_required_context")
            budget = self.model_context.context_budget()

            def measure(chosen):
                return self.model_context.estimate_context(
                    organize_context(chosen, self.settings.context_policy),
                    request.input.tools, run_id=request.run_id
                )

            if (
                self.streams
                and self.settings.progress.enabled
                and measure(items).tokens > budget.warning
            ):
                try:
                    self.streams.emit_stream(
                        StreamEvent(
                            type="progress",
                            run_id=request.run_id,
                            stage="context_compaction",
                            label=self.settings.progress.labels["context_compaction"],
                        )
                    )
                except Exception:
                    pass
            selected = select_context(
                items, budget, measure, required, self.settings.context_policy
            )
            selected.items = organize_context(selected.items, self.settings.context_policy)
        except OverflowError as exc:
            return ModuleResponse(
                status="ok",
                output=PerceivedContext(
                    run_id=request.run_id,
                    items=organize_context(exc.items, self.settings.context_policy),
                    needs_compaction=True,
                    assembly=ContextAssembly(
                        budget=budget,
                        used_tokens=getattr(exc, "used_tokens", 0),
                        notices=["context_budget_exceeded"],
                        required_item_ids=sorted(required),
                        trace={
                            "initial_tokens": getattr(exc, "initial_tokens", None),
                            "decisions": getattr(exc, "decisions", []),
                            "stopped_at": "bounded_selection",
                        },
                    ),
                ),
            )
        except PortError as exc:
            return ModuleResponse(status="failed", error=exc.error)
        except (ValueError, TypeError, KeyError):
            return ModuleResponse(
                status="failed", error=self.settings.error("invalid_context")
            )
        return ModuleResponse(
            status="ok",
            output=PerceivedContext(
                run_id=request.run_id,
                items=selected.items,
                assembly=ContextAssembly(
                    budget=budget,
                    used_tokens=selected.used_tokens,
                    selection_policy="identity_then_required_then_complete_units",
                    selected_item_ids=[i.id for i in selected.items],
                    required_item_ids=sorted(required),
                    omitted_item_ids=selected.omitted_item_ids,
                    omitted_reasons={
                        i: "complete_request_budget" for i in selected.omitted_item_ids
                    },
                    notices=["context_compacted"] if selected.compacted else [],
                    trace={
                        "decisions": selected.decisions,
                        "candidate_ids": [i.id for i in items],
                        "memory_selected_ids": memory.selected_item_ids,
                        "elapsed_ms": round((perf_counter() - began) * 1000, 3),
                    },
                ),
            ),
        )
