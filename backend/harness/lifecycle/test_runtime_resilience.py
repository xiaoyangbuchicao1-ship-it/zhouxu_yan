"""Regression tests for tool interruption, bounded restart requeue, greeting routing, and bash timeouts."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.harness.lifecycle import PersistedSession, RunEnvelope, SessionSnapshot
from backend.contracts.model_context import RequestBudget, RequestEstimate
from backend.contracts.modules.loop import ActionRequest, DecisionProposal, RunInput, RunResult
from backend.harness.lifecycle.executor import HarnessRunExecutor
from backend.harness.lifecycle.recovery import recovery_for_run
from backend.harness.lifecycle.scheduler import InMemoryRunScheduler
from backend.infrastructure.execution.workspace import WorkspaceExecutionProvider
from backend.infrastructure.persistence.run_store import SQLiteRunStore
from backend.interfaces.api.app import EventHub
from backend.interfaces.api.control_plane import HarnessControlService


class _FakeModel:
    def __init__(self, script):
        self._script = list(script)
        self.seen_inputs = []

    def context_budget(self) -> RequestBudget:
        return RequestBudget(
            model_id="test-model",
            purpose="main",
            raw_tokens=64000,
            effective_tokens=64000,
            input_limit=48000,
            output_reserved=8192,
            safety_tokens=2048,
            hard=40000,
            target=28000,
            warning=32000,
            low=24000,
            counting_method="utf8_upper_bound_estimate",
        )

    def estimate_context(self, items, tools, *, run_id: str = "") -> RequestEstimate:
        return RequestEstimate(tokens=800, method="utf8_upper_bound_estimate")

    def allocate_tool_context(
        self, used_tokens: int, call_count: int, image_count: int = 0
    ) -> dict[str, int]:
        return {"tokens": 4000, "chars": 12000}

    def supports_images(self) -> bool:
        return False

    async def decide(self, request):
        self.seen_inputs.append(request.input)
        step = self._script.pop(0)
        return step(request) if callable(step) else step


class _FakeCommandRuntime:
    def __init__(self, *, sleep_seconds: float = 10.0):
        self.sleep_seconds = sleep_seconds
        self.started = threading.Event()
        self.closed = threading.Event()
        self.seen_timeouts: list[float | None] = []

    def exec(self, args, *, timeout=None, check=True, input=None):
        return subprocess.CompletedProcess(args, 0, b"", b"")

    def python(self, code, args=None, *, timeout=None, check=True, input=None):
        return subprocess.CompletedProcess(["python"], 0, b"{}", b"")

    def bash(self, command, *, timeout=None, check=True):
        self.seen_timeouts.append(timeout)
        self.started.set()
        if self.closed.wait(timeout=self.sleep_seconds):
            return subprocess.CompletedProcess(["bash"], -9, b"", b"killed")
        return subprocess.CompletedProcess(["bash"], 0, b"done\n", b"")

    def close(self):
        self.closed.set()


class _FakeExecutionProvider(WorkspaceExecutionProvider):
    def __init__(self, settings, *, sleep_seconds: float = 10.0):
        super().__init__(settings)
        self.sleep_seconds = sleep_seconds
        self.runtimes: list[_FakeCommandRuntime] = []

    def _create_runtime(self, run_id: str, root: Path):
        runtime = _FakeCommandRuntime(sleep_seconds=self.sleep_seconds)
        self.runtimes.append(runtime)
        return runtime


def _test_settings(tmp: Path):
    settings = load_settings()
    settings.web.enabled = False
    settings.rag_tool.enabled = False
    settings.cases.enabled = False
    settings.research_cards.enabled = False
    settings.execution.workspace_root = str(tmp / "workspaces")
    return settings


class RuntimeResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_interrupts_running_bash_and_unblocks_next_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _test_settings(Path(tmp))
            execution = _FakeExecutionProvider(settings, sleep_seconds=15.0)
            model = _FakeModel(
                [
                    DecisionProposal(
                        kind="action",
                        action_requests=[
                            ActionRequest(
                                tool_name="bash",
                                arguments={"command": "sleep 15", "timeout_seconds": 60},
                                action_id="bash-1",
                            )
                        ],
                    ),
                    DecisionProposal(kind="answer", answer="第二条消息立刻完成", complete=True),
                ]
            )
            events = EventHub()
            harness = build_harness(
                model=model,
                settings=settings,
                events=events,
                streams=events,
                execution_tools=execution,
            )
            scheduler = InMemoryRunScheduler(HarnessRunExecutor(harness, execution_tools=execution))
            first = await scheduler.submit(
                session_id="s-cancel",
                payload=RunInput(text="跑一个耗时命令", mode="work"),
            )
            for _ in range(100):
                if execution.runtimes and execution.runtimes[0].started.is_set():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(execution.runtimes and execution.runtimes[0].started.is_set())

            second = await scheduler.submit(
                session_id="s-cancel",
                payload=RunInput(text="下一条消息", mode="work"),
            )
            started_cancel = time.monotonic()
            await scheduler.cancel(session_id="s-cancel", run_id=first.run_id)
            await asyncio.wait_for(scheduler.wait_for_idle(session_id="s-cancel"), timeout=3.0)
            elapsed = time.monotonic() - started_cancel

            self.assertLess(elapsed, 2.5)
            self.assertTrue(execution.runtimes[0].closed.is_set())
            first_run = await scheduler.get_run(session_id="s-cancel", run_id=first.run_id)
            second_run = await scheduler.get_run(session_id="s-cancel", run_id=second.run_id)
            self.assertEqual(first_run.status, "cancelled")
            self.assertEqual(second_run.status, "succeeded")
            self.assertEqual(second_run.output, "第二条消息立刻完成")

    async def test_pause_interrupts_running_bash_and_paused_run_can_be_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _test_settings(Path(tmp))
            execution = _FakeExecutionProvider(settings, sleep_seconds=15.0)
            model = _FakeModel(
                [
                    DecisionProposal(
                        kind="action",
                        action_requests=[
                            ActionRequest(
                                tool_name="bash",
                                arguments={"command": "sleep 15", "timeout_seconds": 60},
                                action_id="bash-pause",
                            )
                        ],
                    ),
                ]
            )
            events = EventHub()
            harness = build_harness(
                model=model,
                settings=settings,
                events=events,
                streams=events,
                execution_tools=execution,
            )
            scheduler = InMemoryRunScheduler(HarnessRunExecutor(harness, execution_tools=execution))
            dispatched = await scheduler.submit(
                session_id="s-pause",
                payload=RunInput(text="跑一个长命令然后暂停", mode="work"),
            )
            for _ in range(100):
                if execution.runtimes and execution.runtimes[0].started.is_set():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(execution.runtimes and execution.runtimes[0].started.is_set())

            began = time.monotonic()
            await scheduler.pause(session_id="s-pause", run_id=dispatched.run_id)
            await asyncio.wait_for(scheduler.wait_for_idle(session_id="s-pause"), timeout=3.0)
            self.assertLess(time.monotonic() - began, 2.5)
            self.assertTrue(execution.runtimes[0].closed.is_set())

            paused_run = await scheduler.get_run(session_id="s-pause", run_id=dispatched.run_id)
            self.assertEqual(paused_run.status, "paused")

            await scheduler.cancel(session_id="s-pause", run_id=dispatched.run_id)
            cancelled_run = await scheduler.get_run(session_id="s-pause", run_id=dispatched.run_id)
            self.assertEqual(cancelled_run.status, "cancelled")
            self.assertIsNotNone(cancelled_run.completed_at)

    async def test_restart_auto_requeue_is_bounded_and_allows_manual_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteRunStore(Path(tmp) / "runs.sqlite3")
            attempts: list[str] = []

            class _CrashingExecutor:
                async def execute(self, run: RunEnvelope, control):
                    attempts.append(run.run_id)
                    await asyncio.Event().wait()

            run = RunEnvelope(
                run_id="run-crash-loop",
                session_id="s-restart",
                status="running",
                payload=RunInput(text="会崩溃的任务", mode="work").model_dump(),
            )
            await store.save(
                PersistedSession(
                    snapshot=SessionSnapshot(
                        session_id="s-restart",
                        active_run_id=run.run_id,
                        runs=[run],
                    )
                )
            )

            # First restart: auto-requeue once (budget = 1).
            scheduler_1 = InMemoryRunScheduler(_CrashingExecutor(), store, max_restart_requeues=1)
            await scheduler_1.recover()
            await asyncio.sleep(0.05)
            self.assertEqual(attempts, ["run-crash-loop"])
            persisted_after_1 = await scheduler_1.get_run(
                session_id="s-restart", run_id="run-crash-loop"
            )
            self.assertEqual(persisted_after_1.status, "running")
            self.assertEqual(persisted_after_1.restart_requeue_count, 1)
            await scheduler_1.shutdown()

            # Second restart while still running: stop auto-requeueing, fail with restart_interrupted.
            scheduler_2 = InMemoryRunScheduler(_CrashingExecutor(), store, max_restart_requeues=1)
            await scheduler_2.recover()
            await asyncio.sleep(0.05)
            self.assertEqual(attempts, ["run-crash-loop"])
            stopped = await scheduler_2.get_run(session_id="s-restart", run_id="run-crash-loop")
            self.assertEqual(stopped.status, "failed")
            self.assertIsNotNone(stopped.error)
            self.assertEqual(stopped.error.code, "restart_interrupted")
            recovery = recovery_for_run(stopped)
            self.assertTrue(recovery.can_continue)
            self.assertTrue(recovery.can_retry)

            # Manual continue resets the restart counter and succeeds cleanly.
            class _SucceedingExecutor:
                async def execute(self, run: RunEnvelope, control):
                    return RunResult(
                        run_id=run.run_id,
                        status="succeeded",
                        answer="手动继续后完成",
                        elapsed_seconds=0.1,
                    )

            scheduler_3 = InMemoryRunScheduler(_SucceedingExecutor(), store, max_restart_requeues=1)
            await scheduler_3.recover()
            await scheduler_3.continue_run(session_id="s-restart", run_id="run-crash-loop")
            await scheduler_3.wait_for_idle(session_id="s-restart")
            resumed = await scheduler_3.get_run(session_id="s-restart", run_id="run-crash-loop")
            self.assertEqual(resumed.status, "succeeded")
            self.assertEqual(resumed.restart_requeue_count, 0)
            self.assertEqual(resumed.output, "手动继续后完成")
            store.close()

    async def test_bash_timeout_is_not_cut_off_by_40s_action_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _test_settings(Path(tmp))
            # Simulate an outer action_timeout_seconds much smaller than bash's requested timeout.
            settings.runtime.action_timeout_seconds = 0.08
            settings.execution.command_timeout_seconds = 120.0
            execution = _FakeExecutionProvider(settings, sleep_seconds=0.25)
            model = _FakeModel(
                [
                    DecisionProposal(
                        kind="action",
                        action_requests=[
                            ActionRequest(
                                tool_name="bash",
                                arguments={"command": "echo long-task", "timeout_seconds": 120},
                                action_id="bash-long",
                            )
                        ],
                    ),
                    DecisionProposal(kind="answer", answer="命令顺利跑完", complete=True),
                ]
            )
            events = EventHub()
            harness = build_harness(
                model=model,
                settings=settings,
                events=events,
                streams=events,
                execution_tools=execution,
            )
            result = await harness.run(RunInput(text="运行耗时文档转换命令", mode="work"))
            self.assertEqual(result.status, "succeeded", result.error)
            self.assertEqual(result.answer, "命令顺利跑完")
            self.assertEqual(execution.runtimes[0].seen_timeouts, [120])

    async def test_greeting_uses_harness_without_booting_execution_sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _test_settings(Path(tmp))
            execution = _FakeExecutionProvider(settings, sleep_seconds=0.1)
            model = _FakeModel(
                [DecisionProposal(kind="answer", answer="你好，我在，有什么可以帮你？", complete=True)]
            )
            events = EventHub()
            harness = build_harness(
                model=model,
                settings=settings,
                events=events,
                streams=events,
                execution_tools=execution,
            )
            service = HarnessControlService(
                HarnessRunExecutor(harness, execution_tools=execution)
            )
            dispatch, run = await service.submit(
                session_id="s-hello",
                payload=RunInput(text="你好", mode="chat"),
            )
            self.assertIsNone(run.graph)
            await service.wait_for_idle(session_id="s-hello")
            done = await service.run(session_id="s-hello", run_id=dispatch.run_id)
            self.assertEqual(done.status, "succeeded")
            self.assertEqual(done.output, "你好，我在，有什么可以帮你？")
            # Sandbox must never be instantiated for a plain greeting.
            self.assertEqual(execution.runtimes, [])


if __name__ == "__main__":
    unittest.main()
