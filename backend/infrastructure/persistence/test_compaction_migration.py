import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from backend.contracts.harness.execution import ExecutionState
from backend.infrastructure.persistence.migrate_compaction_counts import database_digest, migrate


class CompactionMigrationTests(unittest.TestCase):
    def fixture(self, root):
        summary = {"record_id": "summary-1", "source": "memory", "run_id": "run",
                   "body": {"text": "关键事实已保留。", "covered_record_ids": ["record-1"]}}
        reference = {"record_id": "summary-2", "source": "memory", "run_id": "run",
                     "body": {"text": '{"status":"reference_only"}', "covered_record_ids": ["record-2"]}}
        other_run = {**summary, "record_id": "summary-other", "run_id": "older-run"}
        run = {"run_id": "run", "session_id": "conversation", "status": "paused",
               "payload": {"text": "用户原文，必须保留"}, "output": "原有回答",
               "context_compaction_count": 0,
               "execution_state": {"schema_version": 2,
                   "records": [summary, reference, other_run, summary],
                   "artifacts": [{"artifact_id": "file", "relative_path": "answer.md", "sha256": "original"}]}}
        runtime, workspace = root / "runs.sqlite3", root / "zhouxu.db"
        event = '{"type":"progress","stage":"context_compaction","sequence":9}'
        with sqlite3.connect(runtime) as db:
            db.execute("CREATE TABLE run_sessions(id TEXT PRIMARY KEY,body TEXT)")
            db.execute("CREATE TABLE run_events(run_id TEXT,sequence INTEGER,body TEXT)")
            db.execute("CREATE TABLE run_visible(run_id TEXT,text TEXT,sequence INTEGER)")
            db.execute("INSERT INTO run_sessions VALUES ('conversation',?)", (json.dumps({"snapshot": {"runs": [run]}}),))
            db.execute("INSERT INTO run_events VALUES ('run',9,?)", (event,))
            db.execute("INSERT INTO run_visible VALUES ('run','回答正文',9)")
        with sqlite3.connect(workspace) as db:
            db.execute("CREATE TABLE conversation_runs(run_id TEXT PRIMARY KEY,progress_json TEXT,answer TEXT)")
            db.execute("INSERT INTO conversation_runs VALUES ('run',?, '用户回答')", (json.dumps({"source_refs": [{"source_id": "original-source"}]}),))
            db.execute("INSERT INTO conversation_runs VALUES ('projection-only','{}','旧回答')")
        (root / "answer.md").write_text("用户文件", encoding="utf-8")
        return runtime, workspace, run, event

    def test_read_only_preflight_paired_backups_and_idempotent_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, workspace, original, event = self.fixture(root)
            before = [path.read_bytes() for path in (runtime, workspace)]
            report = migrate(runtime, workspace)
            self.assertEqual((report["runs"], report["projections"], report["confirmed_compactions"]), (1, 2, 1))
            self.assertFalse(report["applied"])
            self.assertEqual([path.read_bytes() for path in (runtime, workspace)], before)
            backup_dir = root / "backups"
            self.assertTrue(migrate(runtime, workspace, backup_dir=backup_dir)["applied"])
            for name, original_bytes in zip(("runs.sqlite3", "zhouxu.db"), before, strict=True):
                copy = root / ("before-" + name)
                copy.write_bytes(original_bytes)
                with sqlite3.connect(copy) as source, sqlite3.connect(backup_dir / name) as backup:
                    self.assertEqual(database_digest(source), database_digest(backup))
            with sqlite3.connect(runtime) as db:
                saved = json.loads(db.execute("SELECT body FROM run_sessions").fetchone()[0])["snapshot"]["runs"][0]
                self.assertEqual(saved["context_compaction_count"], 1)
                self.assertEqual(saved["execution_state"].pop("context_compaction_count"), 1)
                self.assertEqual(saved["execution_state"], original["execution_state"])
                self.assertEqual(saved["payload"], original["payload"])
                self.assertEqual(db.execute("SELECT sequence,body FROM run_events").fetchone(), (9, event))
                self.assertEqual(db.execute("SELECT text,sequence FROM run_visible").fetchone(), ("回答正文", 9))
            with sqlite3.connect(workspace) as db:
                rows = {row[0]: json.loads(row[1]) for row in db.execute("SELECT run_id,progress_json FROM conversation_runs")}
                self.assertEqual(rows["run"], {"source_refs": [{"source_id": "original-source"}], "context_compaction_count": 1})
                self.assertEqual(rows["projection-only"], {"context_compaction_count": 0})
            self.assertEqual((root / "answer.md").read_text(encoding="utf-8"), "用户文件")
            repeated = migrate(runtime, workspace, backup_dir=root / "unused-backups")
            self.assertEqual((repeated["runs"], repeated["projections"], repeated["applied"]), (0, 0, False))
            self.assertFalse((root / "unused-backups").exists())

    def test_conflicting_counter_is_rejected_without_overwriting_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, workspace, _, _ = self.fixture(root)
            with sqlite3.connect(runtime) as db:
                raw = json.loads(db.execute("SELECT body FROM run_sessions").fetchone()[0])
                raw["snapshot"]["runs"][0]["context_compaction_count"] = 9
                db.execute("UPDATE run_sessions SET body=?", (json.dumps(raw),))
            before = [path.read_bytes() for path in (runtime, workspace)]
            with self.assertRaisesRegex(ValueError, "without_retained_evidence"):
                migrate(runtime, workspace, backup_dir=root / "backup")
            self.assertEqual([path.read_bytes() for path in (runtime, workspace)], before)
            self.assertFalse((root / "backup").exists())

    def test_typed_checkpoint_keeps_count_and_rejects_invalid_values(self):
        self.assertEqual(ExecutionState.model_validate({"context_compaction_count": 3}).model_dump()["context_compaction_count"], 3)
        for value in (-1, True, "3"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ExecutionState.model_validate({"context_compaction_count": value})


if __name__ == "__main__":
    unittest.main()
