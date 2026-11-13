"""Model-facing read-only adapter for the declarative Skill registry.

``Skill`` remains a method/resource object. ``read_skill`` is the Tool
operation that loads one registered Skill into the model context.
"""

from backend.config.settings import Settings
from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.skills.registry import SkillNotFound, SkillRegistry


class SkillReadTool:
    def __init__(self, registry: SkillRegistry, settings: Settings):
        self.registry, self.settings = registry, settings

    @property
    def spec(self) -> ToolSpec:
        descriptors = self.registry.descriptors()
        names = [item.name for item in descriptors]
        catalog = "\n".join(
            f"- {item.name} (v{item.version}): {item.description}"
            for item in descriptors
        )
        return ToolSpec(
            name="read_skill",
            description=(
                "读取已注册的专业 Skill 规范。遇到具体海事法律问答、法规版本、"
                "海事案例或其他工具描述明确匹配的专业流程时，先读取对应 Skill；"
                "Skill 只是流程说明，不是本次问题的证据。只能选择已注册名称，"
                "不要传路径、URL 或内部文件名。\n\n"
                "可用 Skill：\n"
                f"{catalog}"
            ),
            parameters={
                "type": "object",
                "properties": {"skill_id": {"type": "string", "enum": names}},
                "required": ["skill_id"],
                "additionalProperties": False,
            },
            read_only=True,
            reuse_identical_results=True,
            kind="skill_activation",
        )

    async def execute_context(self, request: ModuleRequest) -> ActionResult:
        try:
            # ``name`` was the v1 argument. Keep accepting it in persisted
            # requests while advertising the clearer skill_id contract now.
            name = request.input.arguments.get("skill_id")
            if name is None:
                name = request.input.arguments.get("name")
            if not isinstance(name, str):
                raise ValueError
            descriptor = self.registry.descriptor(name)
            body = self.registry.read(name)
            method = self.registry.method(name)
        except (SkillNotFound, ValueError, TypeError, AttributeError):
            raise PortError(self.settings.error("invalid_arguments")) from None
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            result={
                "status": "ok",
                "skill_id": descriptor.skill_id,
                "name": descriptor.name,
                "version": descriptor.version,
                "description": descriptor.description,
                "category": method.get("category"),
                "summary": method.get("summary"),
                "purpose": method.get("purpose"),
                "inputs": method.get("inputs", []),
                "deliverables": method.get("deliverables", []),
                "steps": method.get("steps", []),
                "checks": method.get("checks", []),
                "examples": method.get("examples", []),
                "branches": method.get("branches", []),
                "anti_example": method.get("anti_example"),
                "content": body,
            },
            outcome="ok",
            session_summary={
                "status": "ok",
                "skill": descriptor.skill_id,
                "version": descriptor.version,
                "body_currently_visible": True,
            },
        )
