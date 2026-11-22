"""Routing regressions: the chosen Run model must be the adapter actually used."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import SecretStr

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.harness.lifecycle import RunEnvelope
from backend.contracts.model_context import RequestBudget, RequestEstimate
from backend.contracts.modules.loop import DecisionProposal, RunInput
from backend.contracts.runtime import NodeSpec, RunGraph
from backend.contracts.schemas.core import Error, PortError
from backend.harness.graph.executor import GraphRunExecutor
from backend.harness.graph.handlers import build_graph_handlers
from backend.harness.lifecycle.control import RunControl, StopRequested
from backend.infrastructure.model.router import RoutedModel
from backend.infrastructure.observability.memory import MemoryEventSink


class _Adapter:
    def __init__(self, settings, *_):
        self.name = settings.model.name
        self.calls = []
        self.closed = []
        self.pause_once = False

    def context_budget(self):
        return RequestBudget(
            model_id=self.name, purpose="main", raw_tokens=64000,
            effective_tokens=64000, input_limit=48000, output_reserved=8192,
            safety_tokens=2048, hard=40000, target=28000, warning=32000,
            low=24000, counting_method="utf8_upper_bound_estimate",
        )

    def estimate_context(self, items, tools, *, run_id):
        return RequestEstimate(tokens=1200, method="utf8_upper_bound_estimate")

    def supports_images(self):
        return self.name == "relay-test"

    def allocate_tool_context(self, *args):
        return {"tokens": 4000 if self.name == "relay-test" else 2000, "chars": 12000}

    async def decide(self, request):
        self.calls.append(request.run_id)
        if self.pause_once:
            self.pause_once = False
            raise StopRequested
        return DecisionProposal(kind="answer", answer="你好，已完成本次问候。")

    async def decide_stream(self, request, on_delta):
        decision = await self.decide(request)
        await on_delta(decision.answer)
        return decision

    def export_run_state(self, run_id):
        return {"model": self.name}

    def restore_run_state(self, run_id, value):
        if not value:
            return
        if value.get("model") != self.name:
            raise PortError(Error(code="context_configuration", message="模型不匹配"))

    def close_run(self, run_id):
        self.closed.append(run_id)


class ModelRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = load_settings().model_copy(deep=True)
        self.settings.model.name = "deepseek-flash"
        self.settings.model.thinking_mode = "none"
        self.settings.web.enabled = False
        self.settings.materials.cache_root = str(Path(self.tmp.name) / "materials")
        self.events = MemoryEventSink()
        with patch("backend.infrastructure.model.router.DeepSeekModel", _Adapter), patch(
            "backend.infrastructure.model.router.load_key", return_value=SecretStr("test")
        ), patch("backend.infrastructure.model.router.ModelRegistry"):
            self.router = RoutedModel(self.settings, self.events)
        relay_settings = self.settings.model_copy(deep=True)
        relay_settings.model.name = "relay-test"
        self.relay = _Adapter(relay_settings)
        self.router._adapters["relay:relay-test"] = self.relay
        self.router.registry = SimpleNamespace(get=lambda profile: SimpleNamespace(
            provider="relay" if profile.startswith("relay:") else "deepseek",
            configured=True, available=True,
        ))
        self.harness = build_harness(model=self.router, settings=self.settings, events=self.events)

    def graph_executor(self):
        handlers = build_graph_handlers({"none": self.harness}, default_mode="none")
        return GraphRunExecutor(
            handlers.handlers, events=self.events,
            prepare=handlers.prepare, close=handlers.close,
        )

    def graph_run(self):
        return RunEnvelope(
            run_id="graph-run", session_id="graph-session",
            payload=RunInput(text="你好", model_profile_id="relay:relay-test").model_dump(),
            graph=RunGraph(nodes=[NodeSpec(
                id="answer", kind="model", label="回答", config={"instruction": "回复问候"}
            )]),
        )

    async def test_graph_uses_selected_model_before_context_assembly_and_closes_it(self):
        result = await self.graph_executor().execute(self.graph_run(), RunControl())
        self.assertEqual(result.status, "succeeded", result.error)
        self.assertEqual(self.relay.calls, ["graph-run"])
        self.assertEqual(self.router._adapters["deepseek-flash"].calls, [])
        self.assertEqual(self.relay.closed, ["graph-run"])
        self.assertEqual(self.router._run_profiles, {})
        with self.assertRaises(PortError) as caught:
            self.router.context_budget()
        self.assertEqual(caught.exception.error.code, "model_run_unbound")

    async def test_parallel_runs_keep_their_own_budget_and_capabilities(self):
        ready = asyncio.Barrier(2)
        observations = {}

        async def inspect(run_id, profile):
            self.router.bind_run(run_id, profile)
            await ready.wait()
            await asyncio.sleep(0)
            observations[run_id] = (
                self.router.context_budget().model_id,
                self.router.supports_images(),
                self.router.allocate_tool_context(0, 1)["tokens"],
            )
            self.router.close_run(run_id)

        await asyncio.gather(inspect("a", "relay:relay-test"), inspect("b", "deepseek-flash"))
        self.assertEqual(observations["a"], ("relay-test", True, 4000))
        self.assertEqual(observations["b"], ("deepseek-flash", False, 2000))
        self.assertEqual(self.router._run_profiles, {})

    async def test_paused_graph_can_rebind_original_model_on_resume(self):
        self.relay.pause_once = True
        executor = self.graph_executor()
        first = await executor.execute(self.graph_run(), RunControl())
        self.assertEqual(first.status, "cancelled")
        self.assertEqual(self.router._run_profiles, {})
        second = await executor.execute(self.graph_run(), RunControl())
        self.assertEqual(second.status, "succeeded", second.error)
        self.assertEqual(self.relay.calls, ["graph-run", "graph-run"])
        self.assertEqual(self.relay.closed, ["graph-run", "graph-run"])

    async def test_ordinary_harness_uses_and_cleans_the_same_route_contract(self):
        result = await self.harness.run(
            RunInput(text="你好", model_profile_id="relay:relay-test"), run_id="ordinary-run"
        )
        self.assertEqual(result.status, "succeeded", result.error)
        self.assertEqual(self.relay.calls, ["ordinary-run"])
        self.assertEqual(self.relay.closed, ["ordinary-run"])
        self.assertEqual(self.router._run_profiles, {})

    async def test_unbound_and_conflicting_checkpoint_cannot_change_selected_model(self):
        with self.assertRaises(PortError) as caught:
            self.router._adapter_for("missing")
        self.assertEqual(caught.exception.error.code, "model_run_unbound")
        self.router.bind_run("run", "deepseek-flash")
        with self.assertRaises(PortError):
            self.router.bind_run("run", "relay:relay-test")
        with self.assertRaises(PortError):
            self.router.restore_run_state("run", {"provider": "relay", "model": "relay-test"})
        self.assertEqual(self.router.context_budget().model_id, "deepseek-flash")
        self.router.close_run("run")

    async def test_chat_available_relay_executes_work_without_verification_gate(self):
        graph_run = self.graph_run()
        graph_run.payload["mode"] = "work"
        graph_result = await self.graph_executor().execute(graph_run, RunControl())
        self.assertEqual(graph_result.status, "succeeded", graph_result.error)
        result = await self.harness.run(
            RunInput(text="你好", mode="work", model_profile_id="relay:relay-test"),
            run_id="work-run",
        )
        self.assertEqual(result.status, "succeeded", result.error)
        self.assertEqual(self.relay.calls, ["graph-run", "work-run"])


if __name__ == "__main__":
    unittest.main()
