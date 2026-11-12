from __future__ import annotations

import json

from backend.config.settings import Settings
from backend.contracts.modules.loop import ActionRequest, ActionResult, ToolSpec
from backend.contracts.modules.materials import MaterialReadInput
from backend.contracts.schemas.core import Error, ModuleRequest, PortError
from backend.infrastructure.model.reading import reading_view
from backend.infrastructure.parsing.catalog import MaterialCatalog


class MaterialReadTool:
    def __init__(self, catalog: MaterialCatalog, settings: Settings):
        self.catalog, self.settings = catalog, settings
        self.spec = ToolSpec(
            name="read_material",
            description=settings.materials.read_tool_description,
            parameters=MaterialReadInput.model_json_schema(),
            read_only=True,
            reuse_identical_results=True,
        )

    async def execute_context(
        self, request: ModuleRequest[ActionRequest]
    ) -> ActionResult:
        try:
            value = MaterialReadInput.model_validate(request.input.arguments)
            context = request.context or {}
            allocated = min(
                context.get(
                    "material_read_chars",
                    self.settings.context_policy.tool_resource_chars,
                ),
                self.settings.context_policy.tool_resource_chars,
            )
            body_chars = min(
                allocated, value.max_chars or self.settings.materials.read_chars
            )
            # The caller already reserved envelope tokens separately from body
            # capacity. A requested body length must not shrink that allocation.
            policy = self.settings.context_policy
            wire_budget = (
                allocated * policy.tool_char_token_bound + policy.tool_envelope_tokens
            )
            while True:
                output = self.catalog.read(
                    value,
                    run_id=request.run_id,
                    limit=body_chars,
                    image_allowed=bool(context.get("image_allowed", False)),
                    track=False,
                )
                projected = reading_view(self.catalog.projection(output))
                # Model/image adapters account for image capacity separately.
                projected.pop("image_data_url", None)
                content = json.dumps(projected, ensure_ascii=False)
                encoded_bytes = len(
                    json.dumps(content, ensure_ascii=False).encode("utf-8")
                )
                if encoded_bytes <= wire_budget:
                    self.catalog.commit_read(output, run_id=request.run_id)
                    break
                body_chars //= 2
                if body_chars < 1:
                    raise PortError(self.settings.error("material_read_budget"))

        except PortError as exc:
            if exc.error.code not in {
                "material_not_found",
                "material_unit_not_found",
                "material_read_budget",
            }:
                raise
            return ActionResult(
                tool_name=request.input.tool_name,
                action_id=request.input.action_id,
                outcome="error",
                error=exc.error,
                result={
                    "status": "unavailable",
                    "error": exc.error.code,
                    "message": exc.error.message,
                },
                session_summary={"status": "unavailable", "error": exc.error.code},
            )
        except Exception:
            raise PortError(self.settings.error("invalid_arguments")) from None
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            result=self.catalog.projection(output),
            error=Error(code="material_processing_failed", category="system", message=self.settings.messages["material_processing_failed"])
            if output.status in {"failed", "not_parsed"} else None,
            outcome=(
                "error"
                if output.status
                in {
                    "unavailable",
                    "failed",
                    "invalid_cursor",
                    "not_found",
                    "not_parsed",
                }
                else "partial"
                if output.manifest.status == "partial"
                else "empty"
                if not output.units and not output.image_data_url
                else "ok"
            ),
            session_summary={
                "material_id": output.manifest.material_id,
                "unit_ids": [unit.unit_id for unit in output.units],
                "status": output.manifest.status,
                "parse_version": output.manifest.parse_version,
                "coverage": output.coverage,
                "next_cursor": output.next_cursor,
            },
        )
