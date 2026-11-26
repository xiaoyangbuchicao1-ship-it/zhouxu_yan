"""Contracts for explicit graph validation and unified Harness routing."""

import unittest

from backend.application.workspace.task_graph import (
    build_work_graph,
    plan_graph,
    validate_graph_capabilities,
)
from backend.contracts.modules.loop import RunInput
from backend.contracts.runtime import NodeSpec, RunGraph


class TaskGraphTests(unittest.TestCase):
    def test_greetings_and_courtesy_turns_stay_on_unified_harness_loop(self):
        for mode in ("chat", "work"):
            for text in ("你好", "你好！", "谢谢", "hi", "搜索相关法规"):
                self.assertIsNone(
                    plan_graph(RunInput(text=text, mode=mode)),
                    f"expected {text!r} ({mode}) to stay on unified Harness loop",
                )

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

    def test_explicit_graph_is_validated_and_returned(self):
        graph = build_work_graph(RunInput(text="执行显式图", mode="work"))
        self.assertEqual(
            plan_graph(RunInput(text="执行显式图", mode="work"), requested=graph),
            graph,
        )


if __name__ == "__main__":
    unittest.main()
