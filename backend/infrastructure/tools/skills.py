"""Read-only bridge from the fixed Skill catalog to Harness actions."""

from backend.config.settings import Settings
from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.skills.registry import SkillNotFound, SkillRegistry


class SkillReadTool:
    def __init__(self, registry: SkillRegistry, settings: Settings):
        self.registry, self.settings = registry, settings

    @property
    def spec(self) -> ToolSpec:
        names = [item.name for item in self.registry.descriptors()]
        return ToolSpec(
            name="read_skill",
            description=(
                "读取已注册的专业 Skill 规范。遇到具体海事法律问答、法规版本、"
                "海事案例或其他工具描述明确匹配的专业流程时，先读取对应 Skill；"
                "Skill 只是流程说明，不是本次问题的证据。只能选择已注册名称，"
                "不要传路径、URL 或内部文件名。"
            ),
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "enum": names}},
                "required": ["name"],
                "additionalProperties": False,
            },
            read_only=True,
            reuse_identical_results=True,
        )

    async def execute_context(self, request: ModuleRequest) -> ActionResult:
        try:
            name = request.input.arguments.get("name")
            if not isinstance(name, str):
                raise ValueError
            descriptor = self.registry.descriptor(name)
            body = self.registry.read(name)
        except (SkillNotFound, ValueError, TypeError, AttributeError):
            raise PortError(self.settings.error("invalid_arguments")) from None
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            result={
                "status": "ok",
                "name": descriptor.name,
                "description": descriptor.description,
                "content": body,
            },
            outcome="ok",
            session_summary={
                "status": "ok",
                "skill": descriptor.name,
                "body_currently_visible": True,
            },
        )
