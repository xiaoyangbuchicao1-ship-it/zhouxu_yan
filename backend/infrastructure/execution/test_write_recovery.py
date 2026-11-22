import tempfile
import unittest
import sqlite3
from pathlib import Path

from backend.config.settings import load_settings
from backend.contracts.modules.loop import ActionRequest
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.execution.native import NativeProvider


class WriteRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_replayed_append_after_restart_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = load_settings()
            settings.execution.workspace_root = tmp
            provider = NativeProvider(settings)
            def request(action_id, **arguments):
                return ModuleRequest(run_id="run", call_id=action_id,
                    input=ActionRequest(action_id=action_id, tool_name="write", arguments=arguments))
            await provider.execute("write", request("first", path="paper.md", content="第一章\n"))
            append = request("second", path="paper.md", content="第二章\n", append=True)
            result = await provider.execute("write", append)
            # Crash window: file replacement happened, receipt completion and
            # the scheduler checkpoint did not. Preserve the durable intent.
            journal = Path(tmp) / "anonymous/run/.zhouxu-writes.sqlite3"
            with sqlite3.connect(journal) as db:
                db.execute("UPDATE writes SET done=0 WHERE id=?", ("run:second",))
            await provider.close_all()
            provider = NativeProvider(settings)
            try:
                recovered = await provider.recover_write(append)
                self.assertIsNotNone(recovered)
                self.assertEqual(result.result, recovered.result)
                repeated = await provider.execute("write", append)
                self.assertEqual(result.result, repeated.result)
                text = (Path(tmp) / "anonymous/run/paper.md").read_text()
                self.assertEqual(text, "第一章\n第二章\n")
            finally:
                await provider.close_all()
