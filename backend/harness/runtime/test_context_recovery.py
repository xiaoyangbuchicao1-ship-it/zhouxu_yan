import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from backend.contracts.modules.perception import ContextAssembly, ContextItem
from backend.contracts.modules.loop import DecisionProposal
from backend.contracts.schemas.core import PortError
from backend.config.settings import load_settings
from backend.harness.runtime.context_recovery import ContextRecovery, compaction_succeeded


class ContextRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_reference_only_fallback_is_not_reported_as_compaction(self):
        self.assertFalse(compaction_succeeded('{"status":"reference_only"}'))
        self.assertTrue(compaction_succeeded("这是已压缩的摘要正文。"))

    def recovery(self, error):
        model = SimpleNamespace(decide=AsyncMock(side_effect=error))
        context = SimpleNamespace(
            context_budget=lambda: SimpleNamespace(target=600),
            estimate_context=lambda *args, **kwargs: SimpleNamespace(tokens=100),
        )
        catalog = Mock()
        recovery = ContextRecovery(model, context, load_settings(), catalog)
        items = [ContextItem(id=f"record-{i}", type="message", source="memory",
                             kind="history.data", role="user", body={"text": "证据" * 900})
                 for i in range(12)]
        perceived = SimpleNamespace(items=items, assembly=ContextAssembly())
        return recovery, perceived, catalog

    async def test_programming_error_is_not_disguised_as_summary(self):
        recovery, perceived, _ = self.recovery(AttributeError("broken contract"))
        with self.assertRaises(AttributeError):
            await recovery.summarize(perceived, run_id="run-test")

    async def test_provider_failure_preserves_all_records_as_readable_reference(self):
        recovery, perceived, catalog = self.recovery(
            PortError(load_settings().error("model_unavailable")))
        text, ids = await recovery.summarize(perceived, run_id="run-test")
        self.assertEqual(ids, [item.id for item in perceived.items])
        self.assertIn('"status": "reference_only"', text)
        catalog.bind_text.assert_called_once()
        original = catalog.bind_text.call_args.args[2]
        for record_id in ids:
            self.assertIn(record_id, original)

    async def test_cc_switch_completion_rules_reject_partial_or_filtered_summaries(self):
        for reason in ("length", "max_tokens", "content_filter", "refusal", "sensitive"):
            with self.subTest(reason=reason):
                recovery, perceived, catalog = self.recovery(None)
                recovery.model.decide.return_value = DecisionProposal(
                    kind="answer", answer="未完成的摘要", finish_reason=reason,
                )
                text, _ = await recovery.summarize(perceived, run_id="run-test")
                self.assertFalse(compaction_succeeded(text))
                catalog.bind_text.assert_called_once()
