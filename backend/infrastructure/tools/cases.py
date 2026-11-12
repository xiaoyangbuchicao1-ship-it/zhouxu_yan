"""Native case tools: domain data, bounded receipts and run-only body retention."""

import json

import httpx

from backend.config.settings import Settings
from backend.contracts.modules.cases import CaseReadInput, CaseSearchInput
from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.contracts.schemas.core import PortError
from backend.infrastructure.corpus.cases import CaseSourceError


class CaseTool:
    def __init__(self, service, settings: Settings, *, read=False):
        self.service, self.settings, self.read = service, settings, read

    @property
    def spec(self):
        return ToolSpec(
            name="read_case" if self.read else "search_cases",
            description=self.settings.cases.read_description
            if self.read
            else self.settings.cases.search_description,
            parameters=(
                CaseReadInput if self.read else CaseSearchInput
            ).model_json_schema(),
            read_only=True,
            reuse_identical_results=True,
        )

    async def execute_context(self, request):
        allocated = (request.context or {}).get("tool_result_chars")
        budget = min(
            self.settings.cases.result_budget_chars,
            max(0, allocated)
            if allocated is not None
            else self.settings.cases.result_budget_chars,
        )
        if budget < 180:
            raise PortError(self.settings.error("rag_tool_budget"))
        outcome = "ok"
        try:
            args = (CaseReadInput if self.read else CaseSearchInput).model_validate(
                request.input.arguments
            )
            if self.service is None:
                raise RuntimeError("case_service_unavailable")
            if self.read:
                size = min(
                    args.chars or self.settings.cases.read_chars, max(1, budget - 1800)
                )
                result = self.service.read(
                    args.case_id, args.start, size, args.snapshot_id
                )
                while len(json.dumps(result, ensure_ascii=False)) > budget and size > 1:
                    size = max(1, size // 2)
                    result = self.service.read(
                        args.case_id, args.start, size, args.snapshot_id
                    )
            else:
                result = await self.service.search(args.query, args.limit, args.case_id)
                omitted = []
                while (
                    len(json.dumps(result, ensure_ascii=False)) > budget
                    and result["items"]
                ):
                    omitted.append(result["items"].pop()["reference_id"])
                result["omitted_ids"] = omitted
                if omitted:
                    outcome = "partial"
                if omitted and not result["items"]:
                    result = {"status": "budget_insufficient"}
                    outcome = "error"
            if len(json.dumps(result, ensure_ascii=False)) > budget:
                result = {"status": "budget_insufficient"}
                outcome = "error"
            elif result.get("status") == "not_found" or result.get("items") == []:
                outcome = "empty"
            for item in (
                [result] if result.get("reference_id") else result.get("items", [])
            ):
                item["citation"] = f"[案例{item['case_id']}]"
            if len(json.dumps(result, ensure_ascii=False)) > budget:
                result = {"status": "budget_insufficient"}
                outcome = "error"
        except CaseSourceError:
            result = {
                "status": "failed",
                "message": self.settings.messages["case_source_mismatch"],
            }
            outcome = "error"
        except ValueError:
            result = {
                "status": "failed",
                "message": self.settings.messages["invalid_arguments"],
            }
            outcome = "error"
        except (httpx.HTTPError, RuntimeError, PortError, KeyError, TypeError):
            result = {
                "status": "failed",
                "message": self.settings.messages["case_service_unavailable"],
            }
            outcome = "error"
        refs = [
            {
                k: v
                for k, v in item.items()
                if k
                in (
                    "case_id",
                    "snapshot_id",
                    "source_sha256",
                    "reference_id",
                    "title",
                    "start",
                    "end",
                )
            }
            for item in (
                [result] if result.get("reference_id") else result.get("items", [])
            )
        ]
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            result=result,
            outcome=outcome,
            session_summary={
                "status": result["status"],
                "source_type": "case",
                "body_currently_visible": False,
                "historical_read_completed": self.read and result.get("status") == "ok",
                "evidence_refs": refs,
            },
        )
