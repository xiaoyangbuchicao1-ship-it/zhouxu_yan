"""Bridge a retrieval Module port into the existing action-tool interface."""

import asyncio
import json

from pydantic import ValidationError

from backend.config.settings import Settings
from backend.contracts.modules.dynamic_rag import (
    RagInput,
    RagObservation,
    RagOutput,
    RagQueryPlan,
)
from backend.contracts.modules.loop import ActionRequest, ActionResult, ToolSpec
from backend.contracts.ports import Module
from backend.contracts.schemas.core import ModuleRequest, PortError


class RagSearchTool:
    def __init__(self, module: Module, settings: Settings):
        self.module, self.settings = module, settings

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.settings.rag_tool.name,
            description=self.settings.rag_tool.description.format(
                max_calls=self.settings.rag_tool.max_calls_per_run
                or "受总运行轮次和时间预算约束"
            ),
            parameters=RagQueryPlan.model_json_schema(),
            read_only=True,
            reuse_identical_results=True,
        )

    async def execute_context(
        self, request: ModuleRequest[ActionRequest]
    ) -> ActionResult:
        try:
            candidate = RagQueryPlan.model_validate(request.input.arguments)
            value = RagInput(**candidate.model_dump())
        except ValidationError:
            raise PortError(self.settings.error("invalid_arguments")) from None
        if (
            self.settings.rag_tool.max_calls_per_run
            and (request.context or {}).get("tool_call_index", 1)
            > self.settings.rag_tool.max_calls_per_run
        ):
            observation = RagObservation(
                query=value.query,
                filters=value.filters,
                status="failed",
                stop_reason="tool_call_limit",
                error=self.settings.error("rag_call_limit"),
            )
        else:
            try:
                # IDs originate in Harness, not in LLM-generated tool arguments.
                response = await self.module.handle(
                    ModuleRequest(
                        run_id=request.run_id,
                        call_id=request.call_id,
                        turn_id=request.turn_id,
                        input=value,
                        context=request.context,
                    )
                )
                if response.status == "cancelled":
                    raise asyncio.CancelledError
                output = (
                    RagOutput.model_validate(response.output)
                    if response.output is not None
                    else None
                )
                if output is None and response.status == "ok":
                    raise PortError(self.settings.error("missing_output"))
                observation = RagObservation(
                    query=value.query,
                    filters=value.filters,
                    status=response.status,
                    items=output.items if output else [],
                    stop_reason=output.stop_reason if output else "retrieval_failed",
                    sufficiency=output.sufficiency
                    if output and response.status == "ok"
                    else "unassessed",
                    missing_aspects=output.missing_aspects if output else [],
                    notices=output.notices if output else [],
                    omitted_ids=output.omitted_ids if output else [],
                    error=response.error,
                )
            except PortError as exc:
                observation = RagObservation(
                    query=value.query,
                    filters=value.filters,
                    status="failed",
                    stop_reason="retrieval_failed",
                    error=exc.error,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                observation = RagObservation(
                    query=value.query,
                    filters=value.filters,
                    status="failed",
                    stop_reason="retrieval_failed",
                    error=self.settings.error("rag_search_failed"),
                )
        # Resource cap of this bridge, additionally bounded by the current Run.
        observation = self._pack(
            observation, (request.context or {}).get("tool_result_chars")
        )
        # Keep only identities/version metadata after the run. No evidence body
        # or internal review trace is injected twice or persisted as session fact.
        summary = {
            "query": observation.query,
            "filters": observation.filters.model_dump(),
            "status": observation.status,
            "sufficiency": observation.sufficiency,
            "stop_reason": observation.stop_reason,
            "evidence_refs": [
                item.model_dump(
                    include={
                        "chunk_id",
                        "document_id",
                        "parent_id",
                        "document_name",
                        "article_no",
                        "is_current",
                        "version_date",
                        "version_relation",
                        "version_target_document_id",
                        "version_target_parent_id",
                    }
                )
                for item in observation.items
            ],
        }
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            result=self._projection(observation),
            outcome=(
                "partial"
                if observation.status != "ok" and observation.items
                else "error"
                if observation.status != "ok"
                else "partial"
                if "tool_output_budget" in observation.notices
                else "empty"
                if not observation.items
                else "ok"
            ),
            error=observation.error,
            session_summary=summary,
        )

    @staticmethod
    def _projection(value):
        result = value.model_dump(
            mode="json", exclude_none=True, exclude={"items", "omitted_ids", "filters"}
        )
        result["omitted_count"] = len(value.omitted_ids)
        result["items"] = []
        for item in value.items:
            projected = item.model_dump(
                include={"document_name", "article_no", "text", "version_date"},
                exclude_none=True,
            )
            projected["text"] = item.chunk_text
            projected["section_path"] = item.section_path
            projected["coverage"] = {
                "part_number": item.chunk_index + 1,
                "parts": item.chunk_count,
            }
            projected["corpus_version_relation"] = item.version_relation
            projected["source_label"] = " · ".join(
                str(v)
                for v in (item.document_name, item.article_no, item.version_date)
                if v
            )
            projected["corpus_current_label"] = item.is_current
            result["items"].append(projected)
        return result

    def _pack(self, value: RagObservation, allocated_chars=None) -> RagObservation:
        value = value.model_copy(deep=True)
        budget = self.settings.rag_tool.result_budget_chars
        if allocated_chars is not None:
            budget = min(budget, max(0, allocated_chars))

        def size():
            return len(
                json.dumps(
                    value.model_dump(mode="json", exclude_none=True), ensure_ascii=False
                )
            )

        while size() > budget and value.items:
            removed = value.items.pop()
            if removed.chunk_id not in value.omitted_ids:
                value.omitted_ids.append(removed.chunk_id)
            if "tool_output_budget" not in value.notices:
                value.notices.append("tool_output_budget")
            value.sufficiency = "unassessed"
        if size() > budget:
            raise PortError(self.settings.error("rag_tool_budget"))
        return value
