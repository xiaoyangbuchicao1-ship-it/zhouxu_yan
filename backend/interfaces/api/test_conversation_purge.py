import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.config.settings import load_settings
from backend.infrastructure.execution.purge import purge_conversation_files
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.persistence.run_store import SQLiteRunStore
from backend.contracts.events.streaming import StreamEvent


def _settings(workspace_root: Path):
    settings = load_settings()
    return settings.model_copy(
        update={"execution": settings.execution.model_copy(update={"workspace_root": str(workspace_root)})}
    )


class ConversationPurgeTest(unittest.TestCase):
    def test_run_store_delete_session_removes_checkpoints_events_and_visible_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteRunStore(Path(tmp) / "runs.sqlite3")
            body = json.dumps({"snapshot": {"session_id": "c1", "runs": [{"run_id": "r1"}, {"run_id": "r2"}]}})
            with closing(sqlite3.connect(store.path)) as db:
                db.execute("INSERT INTO run_sessions VALUES (?,?)", ("c1", body))
                db.execute("INSERT INTO run_sessions VALUES (?,?)", ("c2", json.dumps({"snapshot": {"runs": [{"run_id": "r9"}]}})))
                for run_id in ("r1", "r2", "r9"):
                    db.execute("INSERT INTO run_events VALUES (?,?,?)", (run_id, 1, "{}"))
                    db.execute("INSERT INTO run_visible VALUES (?,?,?)", (run_id, "机密正文", 1))
                db.commit()
            self.assertEqual(sorted(store.delete_session("c1")), ["r1", "r2"])
            with closing(sqlite3.connect(store.path)) as db:
                self.assertEqual(db.execute("SELECT id FROM run_sessions").fetchall(), [("c2",)])
                self.assertEqual(db.execute("SELECT run_id FROM run_events").fetchall(), [("r9",)])
                self.assertEqual(db.execute("SELECT run_id FROM run_visible").fetchall(), [("r9",)])
            self.assertEqual(store.delete_session("missing"), [])
            store.close()

    def test_files_and_skill_copies_are_removed_but_neighbours_stay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            keep = root / "acct" / "other"
            gone = root / "acct" / "conv"
            copies = root / ".skill-packages" / "acct" / "conv"
            for directory in (keep, gone, copies):
                directory.mkdir(parents=True)
                (directory / "f.txt").write_text("x", encoding="utf-8")
            removed = purge_conversation_files(_settings(root), "acct", "conv")
            self.assertEqual(removed, 2)
            self.assertFalse(gone.exists())
            self.assertFalse(copies.exists())
            self.assertTrue((keep / "f.txt").exists())
            self.assertEqual(purge_conversation_files(_settings(root), "acct", "conv"), 0)

    def test_path_tricks_cannot_reach_outside_the_workspace_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "keep.txt").write_text("x", encoding="utf-8")
            root = Path(tmp) / "root"
            root.mkdir()
            self.assertEqual(purge_conversation_files(_settings(root), "..", "..", ), 0)
            self.assertTrue((outside / "keep.txt").exists())

    def test_memory_sink_forgets_the_discarded_runs_only(self):
        sink = MemoryEventSink()
        for run_id in ("r1", "r2"):
            sink.emit_stream(StreamEvent(run_id=run_id, type="delta", text=run_id))
        sink.discard_runs(["r1"])
        self.assertEqual([event.run_id for event in sink.stream_events], ["r2"])
        self.assertEqual(sink.next_stream_sequence("r1"), 1)


if __name__ == "__main__":
    unittest.main()
