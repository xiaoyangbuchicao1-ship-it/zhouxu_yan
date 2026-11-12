"""Material search is a navigation tool, independent of the law corpus RAG."""

from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.contracts.modules.materials import MaterialSearchInput
from backend.contracts.schemas.core import PortError


class MaterialSearchTool:
    def __init__(self, catalog, settings):
        self.catalog, self.settings = catalog, settings
        self.spec = ToolSpec(
            name="search_material",
            description=settings.materials.search_tool_description,
            parameters=MaterialSearchInput.model_json_schema(),
            read_only=True,
            reuse_identical_results=True,
        )

    async def execute_context(self, request):
        try:
            value = MaterialSearchInput.model_validate(request.input.arguments)
        except ValueError:
            raise PortError(self.settings.error("invalid_arguments")) from None
        context = request.context or {}
        limit = min(
            value.max_chars or self.settings.materials.search_result_chars,
            self.settings.materials.search_result_chars,
            context.get(
                "tool_result_chars", self.settings.materials.search_result_chars
            ),
        )
        result = self.catalog.search(value, run_id=request.run_id, limit=max(0, limit))
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            outcome=(
                "error"
                if result["status"] in {"unavailable", "failed", "invalid_cursor"}
                else "partial"
                if result.get("incomplete_material_ids") or result.get("truncated")
                else "empty"
                if not result.get("matches")
                else "ok"
            ),
            result=result,
            session_summary={
                "query": value.query,
                "status": result["status"],
                "match_refs": [
                    {"material_id": m["material_id"], "unit_id": m["unit_id"]}
                    for m in result.get("matches", [])
                ],
            },
        )
