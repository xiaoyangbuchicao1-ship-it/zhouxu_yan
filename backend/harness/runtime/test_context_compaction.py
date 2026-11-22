import tempfile
import unittest
from pathlib import Path

from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.model_context import RequestBudget, RequestEstimate
from backend.contracts.modules.loop import DecisionProposal, RunInput
from backend.contracts.modules.memory import MemoryRecord
from backend.interfaces.api.app import EventHub


class _CompactionModel:
    def __init__(self):
        self.estimate_calls = 0
        self.decisions = []
        self.estimate_ids = []
        self.did_summary = False

    def context_budget(self):
        return RequestBudget(
            model_id="test-model", purpose="main", raw_tokens=64000,
            effective_tokens=64000, input_limit=48000, output_reserved=8192,
            safety_tokens=2048, hard=40000, target=28000, warning=32000,
            low=24000, counting_method="utf8_upper_bound_estimate",
        )

    def estimate_context(self, items, tools, *, run_id=""):
        self.estimate_calls += 1
        self.estimate_ids.append([getattr(item, "id", None) for item in items])
        # The first complete request is intentionally over the warning line;
        # the compaction envelope and the next request fit after the summary.
        summary_input = any(item_id == "summary-input" for item_id in self.estimate_ids[-1])
        tokens = 1000 if summary_input or self.did_summary else 50000
        return RequestEstimate(tokens=tokens, method="utf8_upper_bound_estimate")

    def allocate_tool_context(self, used_tokens, call_count, image_count=0):
        return {"tokens": 4000, "chars": 12000}

    def supports_images(self):
        return False

    async def decide(self, request):
        self.decisions.append(request.input.purpose)
        if request.input.purpose == "compaction":
            self.did_summary = True
            return DecisionProposal(kind="answer", answer="旧对话摘要：已保留关键事实。")
        return DecisionProposal(kind="answer", answer="继续处理完成。", complete=True)


class ContextCompactionHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_compaction_is_counted_and_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = load_settings()
            settings.web.enabled = False
            settings.rag_tool.enabled = False
            settings.cases.enabled = False
            settings.research_cards.enabled = False
            settings.execution.provider = "native"
            settings.execution.workspace_root = str(root / "workspaces")
            settings.materials.storage_root = str(root / "materials")
            settings.materials.cache_root = str(root / "materials-cache")
            model = _CompactionModel()
            events = EventHub()
            harness = build_harness(model=model, settings=settings, events=events, streams=events)
            history = [MemoryRecord(
                session_id="session-context", run_id="old-run", task_id="old-run",
                source="user", summary="较早的问题", body={"text": "较早的问题"},
            )]
            result = await harness.run(
                RunInput(text="继续回答当前问题", mode="chat"),
                session_id="session-context", run_id="current-run", history=history,
            )

            self.assertEqual(result.status, "succeeded", result.error)
            self.assertEqual(
                result.execution_state["context_compaction_count"], 1,
                f"decisions={model.decisions} estimates={model.estimate_calls} ids={model.estimate_ids}",
            )
            self.assertIn("compaction", model.decisions)
            done = [
                event for event in events.stream_events
                if event.stage == "context_compaction_done"
            ]
            self.assertEqual(len(done), 1)
            self.assertEqual(done[0].label, "已整理较早的对话，继续处理")


if __name__ == "__main__":
    unittest.main()
