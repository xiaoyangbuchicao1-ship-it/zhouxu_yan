"""Read-only maritime search and exact arithmetic tools registered at composition root."""

from backend.application.maritime.calculator import calculate_claim
from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.search.maritime_corpus import MaritimeCorpus


class CorpusTool:
    def __init__(self, corpus=None):
        self.corpus = corpus or MaritimeCorpus()

    spec = ToolSpec(
        name="search_maritime_corpus",
        read_only=True,
        reuse_identical_results=True,
        description="从自研海事法规或案例库检索相关原文摘录。kind=laws/cases。每轮最多检索2次；已有结果足以支持用户问题时直接回答，不要继续换词重复检索。引用时逐字复制返回的 citation 标记（如 [[ZX:0123456789abcdef]]），系统会转换为真实原文链接，禁止自编网址。完整原文可通过引用卡片查看。回答只展示文献标题、条文或章节位置、原文和会影响判断的具体缺口。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2, "maxLength": 500},
                "kind": {"type": "string", "enum": ["laws", "cases"]},
                "current_only": {"type": "boolean"},
            },
            "required": ["query", "kind"],
            "additionalProperties": False,
        },
    )

    async def execute_context(self, request: ModuleRequest):
        arguments = request.input.arguments
        try:
            result = await self.corpus.search(**arguments)
            public_items = []
            # Search builds immutable citation snapshots with the complete
            # source, but the model only needs the relevant hit excerpt.  The
            # old projection copied up to 5,000 characters of every complete
            # document into every turn, exhausting the relay's conservative
            # context budget before it could compose an answer.
            excerpt_budget_bytes = 1000
            excerpt_used_bytes = 0
            for item in result["items"][:4]:
                original = item.get("excerpt") or item.get("text") or ""
                if excerpt_used_bytes >= excerpt_budget_bytes:
                    break
                public = {
                    key: item[key]
                    for key in ("kind", "title", "text", "locator", "citation")
                    if item.get(key) not in (None, "", [])
                }
                # A case locator is an internal chunk position; the case title
                # and complete source are enough for a user-facing answer.
                if item.get("kind") == "cases":
                    public.pop("locator", None)
                remaining = max(0, excerpt_budget_bytes - excerpt_used_bytes)
                excerpt = original.encode("utf-8")[:remaining].decode("utf-8", "ignore")
                excerpt_used_bytes += len(excerpt.encode("utf-8"))
                public["text"] = excerpt
                public["excerpt_truncated"] = len(excerpt) < len(original)
                public_items.append(public)
            result = {
                "query": result.get("query", arguments.get("query", "")),
                "kind": result.get("kind", arguments.get("kind", "laws")),
                "items": public_items,
            }
            return ActionResult(
                tool_name=request.input.tool_name,
                action_id=request.input.action_id,
                result=result,
                outcome="ok" if result["items"] else "empty",
            )
        except (ValueError, LookupError):
            return ActionResult(
                tool_name=request.input.tool_name,
                action_id=request.input.action_id,
                outcome="error",
                result={
                    "status": "unavailable",
                    "notice": "检索未完成；请检查语料索引和查询，不得据此推断不存在依据。",
                },
            )


class ClaimTool:
    spec = ToolSpec(
        name="calculate_maritime_claim",
        read_only=True,
        reuse_identical_results=True,
        description="按明确提供的数量×单价计算索赔项目，精确十进制并按币种分组；不推断责任、汇率或缺失输入。",
        parameters={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "quantity": {"type": "string"},
                            "rate": {"type": "string"},
                            "currency": {"type": "string"},
                        },
                        "required": ["name", "quantity", "rate", "currency"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    )

    async def execute_context(self, request: ModuleRequest):
        try:
            result = calculate_claim(request.input.arguments.get("items"))
            return ActionResult(
                tool_name=request.input.tool_name,
                action_id=request.input.action_id,
                result=result,
            )
        except (ValueError, TypeError):
            return ActionResult(
                tool_name=request.input.tool_name,
                action_id=request.input.action_id,
                outcome="error",
                result={
                    "status": "invalid_input",
                    "notice": "请核实项目、数量、费率与币种，缺失数据不能推定。",
                },
            )
