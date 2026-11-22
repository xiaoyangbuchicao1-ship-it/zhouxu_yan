import unittest
from types import SimpleNamespace

from backend.contracts.harness.lifecycle import RunEnvelope
from backend.contracts.modules.loop import (
    ActionRequest,
    ActionResult,
    DecisionProposal,
    PerceivedContext,
    RunInput,
    ToolSpec,
)
from backend.contracts.modules.perception import ContextAssembly
from backend.contracts.runtime import NodeSpec, RunGraph
from backend.contracts.schemas.core import ModuleResponse
from backend.harness.graph.handlers import HarnessGraphHandlers
from backend.harness.lifecycle.control import RunControl


class _Perception:
    def __init__(self):
        self.requests = []

    async def handle(self, request):
        self.requests.append(request)
        return ModuleResponse(
            status="ok",
            output=PerceivedContext(
                run_id=request.run_id,
                items=[],
                assembly=ContextAssembly(),
            ),
        )


class _Reasoning:
    def __init__(self):
        self.requests = []

    async def handle(self, request):
        self.requests.append(request)
        return ModuleResponse(
            status="ok",
            output=DecisionProposal(kind="answer", answer="已完成"),
        )


class _ReasoningWithTool(_Reasoning):
    async def handle(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModuleResponse(
                status="ok",
                output=DecisionProposal(
                    kind="action",
                    action_requests=[
                        ActionRequest(
                            tool_name="information_card_search",
                            arguments={"query": "海事法规"},
                            action_id="call-1",
                        )
                    ],
                ),
            )
        return ModuleResponse(
            status="ok",
            output=DecisionProposal(kind="answer", answer="已完成"),
        )


class _Action:
    def __init__(self):
        self.tools = {"information_card_search": object()}
        self.requests = []

    async def handle(self, request):
        self.requests.append(request)
        return ModuleResponse(
            status="ok",
            output=ActionResult(
                tool_name=request.input.tool_name,
                action_id=request.input.action_id,
                outcome="ok",
                result={"items": [{"title": "海事法规"}]},
            ),
        )


class GraphToolContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_node_receives_the_same_registered_tools_as_the_harness(self):
        tool = ToolSpec(
            name="information_card_search",
            description="搜索资料",
            parameters={"type": "object", "properties": {}},
            read_only=True,
        )
        perception = _Perception()
        reasoning = _Reasoning()
        harness = SimpleNamespace(
            modules={"perception": perception, "reasoning": reasoning},
            tools=[tool],
            material_tool=None,
            material_search_tool=None,
            model_context=None,
            execution_tools=None,
        )
        handlers = HarnessGraphHandlers({"none": harness}, default_mode="none")
        run = RunEnvelope(
            run_id="graph-tools",
            session_id="session",
            payload=RunInput(text="请查一条资料", mode="work").model_dump(),
            graph=RunGraph(
                nodes=[
                    NodeSpec(
                        id="answer",
                        kind="model",
                        label="回答",
                        config={"instruction": "查找资料后回答"},
                    )
                ]
            ),
        )

        await handlers.model(run.graph.nodes[0], run, {}, RunControl())

        self.assertEqual(
            [item.name for item in perception.requests[0].input.tools],
            [tool.name],
        )
        self.assertEqual(
            [item.name for item in reasoning.requests[0].input.tools],
            [tool.name],
        )

    async def test_model_node_actions_use_the_same_action_request_result_boundary(self):
        tool = ToolSpec(
            name="information_card_search",
            description="搜索资料",
            parameters={"type": "object", "properties": {}},
            read_only=True,
        )
        perception = _Perception()
        reasoning = _ReasoningWithTool()
        action = _Action()
        harness = SimpleNamespace(
            modules={"perception": perception, "reasoning": reasoning, "action": action},
            tools=[tool],
            material_tool=None,
            material_search_tool=None,
            model_context=None,
            execution_tools=None,
            settings=SimpleNamespace(
                runtime=SimpleNamespace(max_rounds=4),
                context_policy=SimpleNamespace(
                    tool_resource_chars=1000,
                    material_excerpt_chars=1000,
                ),
            ),
        )
        handlers = HarnessGraphHandlers({"none": harness}, default_mode="none")
        run = RunEnvelope(
            run_id="graph-action",
            session_id="session",
            payload=RunInput(text="请查一条资料", mode="work").model_dump(),
            graph=RunGraph(
                nodes=[
                    NodeSpec(
                        id="answer",
                        kind="model",
                        label="回答",
                        config={"instruction": "查找资料后回答"},
                    )
                ]
            ),
        )

        result = await handlers.model(run.graph.nodes[0], run, {}, RunControl())

        self.assertEqual(result.summary, "已完成")
        self.assertEqual(len(action.requests), 1)
        self.assertEqual(action.requests[0].input.tool_name, tool.name)
        self.assertEqual(action.requests[0].input.action_id, "call-1")
        self.assertEqual(len(reasoning.requests), 2)
        self.assertEqual(
            [item.name for item in reasoning.requests[1].input.tools],
            [tool.name],
        )
        tool_result = next(
            item for item in perception.requests[1].input.candidates if item.type == "tool_result"
        )
        self.assertEqual(tool_result.body["action_id"], "call-1")


if __name__ == "__main__":
    unittest.main()
