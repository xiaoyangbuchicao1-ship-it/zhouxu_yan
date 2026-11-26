import asyncio
import unittest

from backend.contracts.model_profiles import ModelCapabilityFlags, ModelRunSnapshot
from backend.contracts.modules.loop import RunInput, RunResult
from backend.harness.lifecycle.scheduler import InMemoryRunScheduler


class _HoldingExecutor:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, run, control):
        self.started.set()
        await self.release.wait()
        return RunResult(run_id=run.run_id, status="succeeded", answer="ok", elapsed_seconds=0.0)


class SteerKeepsModelTest(unittest.IsolatedAsyncioTestCase):
    async def test_steering_a_running_run_keeps_its_pinned_model(self):
        snapshot = ModelRunSnapshot(
            profile_id="relay:model-a",
            provider="relay",
            model_id="model-a",
            protocol="openai_chat_completions",
            thinking_mode="low",
            capabilities=ModelCapabilityFlags(),
            checked_on="2026-11-24",
        )
        executor = _HoldingExecutor()
        scheduler = InMemoryRunScheduler(executor)
        payload = RunInput(
            text="原问题",
            mode="chat",
            thinking_mode="low",
            model_profile_id="relay:model-a",
            model_snapshot=snapshot,
        )
        dispatched = await scheduler.submit(session_id="s1", payload=payload, account_id="a1")
        run_id = dispatched.run_id if hasattr(dispatched, "run_id") else dispatched.run.run_id
        await asyncio.wait_for(executor.started.wait(), timeout=2)

        await scheduler.steer(
            session_id="s1",
            run_id=run_id,
            payload=RunInput(text="补充要求", mode="work", thinking_mode="high"),
        )
        run = await scheduler.get_run(session_id="s1", run_id=run_id)
        steered = RunInput.model_validate(run.payload)
        self.assertEqual(steered.text, "补充要求")
        self.assertEqual(steered.model_profile_id, "relay:model-a")
        self.assertEqual(steered.model_snapshot.profile_id, "relay:model-a")
        self.assertEqual(steered.mode, "chat")
        self.assertEqual(steered.thinking_mode, "low")
        executor.release.set()


if __name__ == "__main__":
    unittest.main()
