import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.config.settings import load_settings
from backend.infrastructure.persistence.migrate_execution_checkpoints import migrate


class ExecutionMigrationTests(unittest.TestCase):
    def test_cutover_is_explicit_backed_up_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = load_settings()
            settings.execution.workspace_root = str(root / "files")
            files = root / "files/account/conversation"
            files.mkdir(parents=True)
            (files / "existing.md").write_text("existing user file")
            run = {"run_id": "run", "session_id": "conversation", "account_id": "account",
                   "conversation_id": "conversation", "status": "paused", "payload": {"text": "original"},
                   "execution_state": {"records": [{"original": "evidence"}], "schema_version": 1}}
            body = json.dumps({"snapshot": {"runs": [run]}})
            rp, wp = root / "runs.sqlite3", root / "zhouxu.db"
            with sqlite3.connect(rp) as db:
                db.execute("CREATE TABLE run_sessions(id TEXT PRIMARY KEY, body TEXT)")
                db.execute("CREATE TABLE run_events(run_id TEXT, sequence INTEGER, body TEXT)")
                db.execute("INSERT INTO run_sessions VALUES ('conversation', ?)", (body,))
                db.execute("INSERT INTO run_events VALUES ('run', 9, '{\"text\":\"completed\"}')")
            with sqlite3.connect(wp) as db:
                db.execute("CREATE TABLE conversation_runs(run_id TEXT PRIMARY KEY, progress_json TEXT)")
                db.execute("INSERT INTO conversation_runs VALUES ('run','{}')")
            self.assertEqual(migrate(rp, wp, settings=settings)["runs"], 1)
            with sqlite3.connect(rp) as db:
                self.assertEqual(db.execute("SELECT body FROM run_sessions").fetchone()[0], body)
            backup = root / "backup"
            self.assertTrue(migrate(rp, wp, backup_dir=backup, settings=settings)["applied"])
            with sqlite3.connect(backup / "runs.sqlite3") as db:
                self.assertEqual(db.execute("SELECT body FROM run_sessions").fetchone()[0], body)
            with sqlite3.connect(rp) as db:
                saved = json.loads(db.execute("SELECT body FROM run_sessions").fetchone()[0])["snapshot"]["runs"][0]
                self.assertEqual(saved["payload"], run["payload"])
                self.assertEqual(saved["execution_state"]["records"], run["execution_state"]["records"])
                self.assertEqual(saved["execution_state"]["workspace_baseline_origin"], "migration_cutover")
                self.assertIn("existing.md", saved["execution_state"]["workspace_baseline"])
                self.assertEqual(db.execute("SELECT sequence,body FROM run_events").fetchone(), (9, '{"text":"completed"}'))
            self.assertEqual(migrate(rp, wp, settings=settings)["runs"], 0)
