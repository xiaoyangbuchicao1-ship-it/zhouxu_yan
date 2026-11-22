"""Golden contracts for Node 1's minimum graph planner."""

import unittest

from backend.application.workspace.task_graph import (
    build_chat_graph,
    build_work_graph,
    is_simple_chat,
    plan_graph,
    validate_graph_capabilities,
)
from backend.contracts.modules.loop import RunInput
from backend.contracts.runtime import NodeSpec, RunGraph


class TaskGraphTests(unittest.TestCase):
    def test_greeting_gets_one_model_node_and_long_request_stays_on_harness(self):
        greeting = build_chat_graph(RunInput(text="你好", mode="chat"))
        self.assertIsNotNone(greeting)
        self.assertEqual([(node.kind, node.id) for node in greeting.nodes], [("model", "model-answer")])
        self.assertIsNone(build_chat_graph(RunInput(text="搜索相关法规", mode="chat")))

    def test_work_plan_is_explicit_and_uses_work_mode(self):
        graph = build_work_graph(RunInput(text="生成一份报告", mode="work"))
        self.assertEqual(graph.nodes[0].kind, "model")
        with self.assertRaisesRegex(ValueError, "work_graph_requires_work_mode"):
            build_work_graph(RunInput(text="生成一份报告", mode="chat"))

    def test_chat_graph_rejects_privileged_nodes_before_execution(self):
        graph = RunGraph(nodes=[NodeSpec(id="skill", kind="skill", label="读取 Skill")])
        with self.assertRaisesRegex(ValueError, "work_mode_required"):
            validate_graph_capabilities(graph, RunInput(text="读取方法", mode="chat"))
        self.assertIs(plan_graph(RunInput(text="读取方法", mode="chat"), requested=None), None)

    def test_work_greeting_keeps_the_one_model_presentation(self):
        graph = plan_graph(RunInput(text="你好", mode="work"))
        self.assertIsNotNone(graph)
        self.assertEqual([node.kind for node in graph.nodes], ["model"])

    def test_greeting_classifier_is_narrow(self):
        self.assertTrue(is_simple_chat("你好！"))
        self.assertFalse(is_simple_chat("你好，请搜索海事法规"))


if __name__ == "__main__":
    unittest.main()
