import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.infrastructure.persistence.migrate_paper_sources import migrate


class PaperSourceMigrationTests(unittest.TestCase):
    def test_replaces_display_links_preserves_receipts_and_resume_uses_new_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime, workspace = root / "runs.sqlite3", root / "zhouxu.db"
            reference = {"source_id": "paper-info-atom-old", "kind": "paper", "title": "原论文", "locator": "旧位置"}
            receipt = {"tool_name": "information_card_read", "result": {"card": {"card_id": "info-atom-old", "claim_or_fact": "真实历史回执"}}}
            run = {"run_id": "run", "session_id": "session", "status": "paused", "payload": {"text": "用户原话"}, "output": "原有回答 info-atom-old",
                   "execution_state": {"source_refs": [reference], "tool_results": [receipt], "records": []}}
            before = copy.deepcopy(run)
            with sqlite3.connect(runtime) as db:
                db.execute("CREATE TABLE run_sessions(id TEXT PRIMARY KEY,body TEXT)")
                db.execute("CREATE TABLE run_events(run_id TEXT,sequence INTEGER,body TEXT)")
                db.execute("CREATE TABLE run_visible(run_id TEXT,text TEXT,sequence INTEGER)")
                db.execute("INSERT INTO run_sessions VALUES ('session',?)", (json.dumps({"snapshot": {"runs": [run]}}),))
                db.execute("INSERT INTO run_events VALUES ('run',5,?)", ('{"type":"progress"}',))
                db.execute("INSERT INTO run_visible VALUES ('run','正文',5)")
            with sqlite3.connect(workspace) as db:
                db.execute("CREATE TABLE conversation_runs(run_id TEXT,progress_json TEXT,answer TEXT)")
                db.execute("INSERT INTO conversation_runs VALUES ('run',?,'不改回答')", (json.dumps({"source_refs": [reference]}),))
            retired = root / "retired.jsonl"
            retired.write_text(json.dumps({"card_id": "info-atom-old", "replacement_document_id": "pdf-new"}) + "\n")
            papers = root / "papers.jsonl"
            papers.write_text(json.dumps({"document_id": "pdf-new", "title": "原论文"}) + "\n")
            original_bytes = [path.read_bytes() for path in (runtime, workspace)]
            preview = migrate(runtime, workspace, retired, papers)
            self.assertEqual((preview["runs"], preview["projections"]), (1, 1))
            self.assertFalse(preview["applied"])
            self.assertEqual([path.read_bytes() for path in (runtime, workspace)], original_bytes)
            result = migrate(runtime, workspace, retired, papers, backup_dir=root / "backup")
            self.assertTrue(result["applied"])
            with sqlite3.connect(runtime) as db:
                saved = json.loads(db.execute("SELECT body FROM run_sessions").fetchone()[0])["snapshot"]["runs"][0]
                state = saved["execution_state"]
                self.assertEqual(state["source_refs"][0]["source_id"], "paper-document-pdf-new")
                self.assertIsNone(state["source_refs"][0]["locator"])
                self.assertEqual(state["tool_results"], before["execution_state"]["tool_results"])
                self.assertEqual(saved["output"], before["output"])
                self.assertEqual(saved["payload"], before["payload"])
                self.assertEqual(state["records"][-1]["kind"], "correction")
                self.assertIn("information_card_search", state["records"][-1]["body"]["instruction"])
                self.assertEqual(db.execute("SELECT body FROM run_events").fetchone()[0], '{"type":"progress"}')
            self.assertFalse(migrate(runtime, workspace, retired, papers, backup_dir=root / "unused")["applied"])
            self.assertFalse((root / "unused").exists())


if __name__ == "__main__":
    unittest.main()
