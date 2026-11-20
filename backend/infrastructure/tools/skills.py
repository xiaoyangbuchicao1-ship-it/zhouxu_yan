"""Model-facing read-only adapter for the declarative Skill registry.

``Skill`` remains a method/resource object. ``read_skill`` is the Tool
operation that loads one registered Skill into the model context.
"""

from collections.abc import Callable
from typing import Any

from backend.config.settings import Settings
from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.skills.registry import SkillNotFound, SkillRegistry

AccountSkillLoader = Callable[[str, str, int | None], dict[str, Any]]


class SkillReadTool:
    def __init__(
        self,
        registry: SkillRegistry,
        settings: Settings,
        account_skill_loader: AccountSkillLoader | None = None,
    ):
        self.registry = registry
        self.settings = settings
        self.account_skill_loader = account_skill_loader

    @property
    def spec(self) -> ToolSpec:
        descriptors = self.registry.descriptors()
        catalog = "\n".join(
            f"- {item.name} (v{item.version}): {item.description}"
            for item in descriptors
        )
        return ToolSpec(
            name="read_skill",
            description=(
                "读取已注册的专业 Skill 规范。遇到具体海事法律问答、法规版本、"
                "海事案例或其他工具描述明确匹配的专业流程时，先读取对应 Skill；"
                "如果当前用户事实中有已选 skill_id，必须先读取该 Skill。"
                "Skill 只是流程说明，不是本次问题的证据。只能选择内置或当前账户"
                "有权使用的 Skill，不要传路径、URL 或内部文件名。\n\n"
                "已注册的内置 Skill：\n"
                f"{catalog}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "version": {
                        "anyOf": [
                            {"type": "integer", "minimum": 1},
                            {"type": "string", "minLength": 1, "maxLength": 80},
                        ],
                    },
                    "resource": {
                        "type": "string",
                        "description": "可选：读取该 Skill 返回的 resources 中一个包内资源名称；省略则读取完整 SKILL.md。",
                        "maxLength": 160,
                    },
                },
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
            arguments = request.input.arguments
            name = arguments.get("skill_id")
            if name is None:
                name = arguments.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError
            name = name.strip()
            resource = arguments.get("resource")
            if resource is not None and not isinstance(resource, str):
                raise ValueError
            resources: tuple[str, ...] = ()
            runtime_instructions = ""
            version = arguments.get("version")
            user_facts = (request.context or {}).get("user_facts", {})
            if not isinstance(user_facts, dict):
                user_facts = {}
            if version is None and user_facts.get("skill_id") == name:
                version = user_facts.get("skill_version")
            if version is not None and not (
                (type(version) is int and version >= 1)
                or (isinstance(version, str) and 1 <= len(version.strip()) <= 80)
            ):
                raise ValueError

            if self.registry.contains(name):
                descriptor = self.registry.descriptor(name)
                if version is not None and str(version) != descriptor.version:
                    raise SkillNotFound("skill_version_not_found")
                body = self.registry.read_resource(name, resource) if resource else self.registry.read(name)
                resources = self.registry.resources(name)
                if name == "csl-law-paper-generator":
                    runtime_instructions = (
                        "舟序运行适配：先按选题调用 capability_card_select，匹配时用 capability_card_read "
                        "读取方法；用 information_card_search/read 获取论文观点，法规与案例分别用现有工具核对。"
                        "没有匹配方法时继续研究，不套用无关能力卡。包内引注指南可通过 read_skill 的 resource 参数读取。"
                        "用 write 写入当前工作区的 .typ 源码，再调用 paper_compile(source_path=相对路径)。"
                        "paper_compile 执行本 Skill 原始固定脚本并提供模板；不要用 bash 猜测宿主路径或自行重写编译器。"
                        "只有双端编译成功才能交付 PDF 和 Word；错误信息可用于修正源稿后重试。"
                        "引注中的作者、出版信息、案号和具体页码必须有真实来源；卡片没有页码时不能据此编造印刷页码。"
                        "能力卡、信息卡、工具名和检索状态只供内部组织工作，不写进论文正文。"
                    )
                method = self.registry.method(name)
                skill_name = descriptor.name
                skill_id = descriptor.skill_id
                skill_version = descriptor.version
                skill_description = descriptor.description
                source = "builtin"
            else:
                if resource:
                    raise SkillNotFound("skill_resource_not_found")
                account_id = (request.context or {}).get("account_id")
                if (
                    not isinstance(account_id, str)
                    or not account_id
                    or self.account_skill_loader is None
                ):
                    raise SkillNotFound("skill_not_found")
                selected = self.account_skill_loader(
                    account_id, name, int(version) if version is not None else None
                )
                if not isinstance(selected, dict):
                    raise SkillNotFound("skill_not_found")
                body = selected.get("instructions")
                skill_id = selected.get("id")
                skill_name = selected.get("name")
                skill_version = selected.get("version")
                skill_description = selected.get("summary", "")
                if (
                    not isinstance(body, str)
                    or not body.strip()
                    or not isinstance(skill_id, str)
                    or not isinstance(skill_name, str)
                    or type(skill_version) is not int
                ):
                    raise SkillNotFound("skill_invalid")
                method = {}
                source = "account"
        except (SkillNotFound, ValueError, TypeError, AttributeError):
            raise PortError(self.settings.error("invalid_arguments")) from None
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            result={
                "status": "ok",
                "skill_id": skill_id,
                "name": skill_name,
                "version": str(skill_version),
                "description": skill_description,
                "source": source,
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
                "resource": resource,
                "resources": list(resources),
                "runtime_instructions": runtime_instructions,
            },
            outcome="ok",
            session_summary={
                "status": "ok",
                "skill": skill_id,
                "version": str(skill_version),
                "source": source,
                "body_currently_visible": True,
            },
        )
