import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from backend.application.workspace.run_projection import RunProjection
from backend.contracts.harness.lifecycle import RunEnvelope
from backend.contracts.modules.loop import RunInput


class _Workspace:
    def __init__(self):
        self.calls = []

    def record_run(self, **kwargs):
        self.calls.append(kwargs)


class _Events:
    def visible(self, run_id):
        return "回答", 1

    def stream_events_since(self, *, run_id):
        return []

    def emit_stream(self, event):
        return event


class CompactionProjectionTests(unittest.TestCase):
    def test_count_is_saved_in_workspace_progress_for_history(self):
        workspace = _Workspace()
        projection = RunProjection(workspace, _Events())
        payload = RunInput(text="继续", mode="chat").model_dump(mode="json")
        run = RunEnvelope(
            run_id="run-compaction",
            session_id="session-compaction",
            conversation_id="conversation-compaction",
            account_id="account-compaction",
            payload=payload,
            status="succeeded",
            output="回答",
            created_at=datetime.now(timezone.utc),
            context_compaction_count=2,
            execution_state={"context_compaction_count": 2},
        )

        projection(run)

        self.assertEqual(len(workspace.calls), 1)
        self.assertEqual(
            workspace.calls[0]["progress"]["context_compaction_count"], 2
        )

    def test_old_run_without_count_projects_zero(self):
        workspace = _Workspace()
        projection = RunProjection(workspace, _Events())
        payload = RunInput(text="你好", mode="chat").model_dump(mode="json")
        run = RunEnvelope(
            run_id="run-old",
            session_id="session-old",
            conversation_id="conversation-old",
            account_id="account-old",
            payload=payload,
            status="succeeded",
            output="你好",
        )

        projection(run)

        self.assertEqual(workspace.calls[0]["progress"]["context_compaction_count"], 0)


if __name__ == "__main__":
    unittest.main()
