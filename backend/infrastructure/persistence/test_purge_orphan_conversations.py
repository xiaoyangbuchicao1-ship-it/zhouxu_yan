import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.infrastructure.persistence.purge_orphan_conversations import purge

LIVE = "a" * 32
GONE = "b" * 32


class PurgeOrphanConversationsTest(unittest.TestCase):
    def test_only_conversations_missing_from_the_business_database_are_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            business = root / "business.sqlite3"
            runs = root / "runs.sqlite3"
            workspace = root / "run-workspaces"
            with closing(sqlite3.connect(business)) as db:
                db.execute("CREATE TABLE conversations (id TEXT PRIMARY KEY)")
                db.execute("INSERT INTO conversations VALUES (?)", (LIVE,))
                db.commit()
            with closing(sqlite3.connect(runs)) as db:
                db.execute("CREATE TABLE run_sessions (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
                db.execute("CREATE TABLE run_events (run_id TEXT, sequence INTEGER, body TEXT)")
                db.execute("CREATE TABLE run_visible (run_id TEXT PRIMARY KEY, text TEXT, sequence INTEGER)")
                for session_id, run_id in ((LIVE, "r-live"), (GONE, "r-gone")):
                    db.execute("INSERT INTO run_sessions VALUES (?,?)", (session_id, json.dumps({"snapshot": {"runs": [{"run_id": run_id}]}})))
                    db.execute("INSERT INTO run_events VALUES (?,?,?)", (run_id, 1, "{}"))
                    db.execute("INSERT INTO run_visible VALUES (?,?,?)", (run_id, "正文", 1))
                db.execute("INSERT INTO run_sessions VALUES (?,?)", ("ephemeral:x", json.dumps({"snapshot": {"runs": []}})))
                db.commit()
            for relative in (f"acct/{LIVE}", f"acct/{GONE}", f".skill-packages/acct/{GONE}", "acct/notes"):
                directory = workspace / relative
                directory.mkdir(parents=True)
                (directory / "f.txt").write_text("x", encoding="utf-8")

            preview = purge(business, runs, workspace)
            self.assertEqual((preview["orphan_sessions"], preview["orphan_runs"], preview["orphan_directories"]), (1, 1, 2))
            self.assertFalse(preview["applied"])
            self.assertTrue((workspace / "acct" / GONE).exists())

            done = purge(business, runs, workspace, apply=True)
            self.assertTrue(done["applied"])
            self.assertFalse((workspace / "acct" / GONE).exists())
            self.assertFalse((workspace / ".skill-packages" / "acct" / GONE).exists())
            self.assertTrue((workspace / "acct" / LIVE).exists())
            self.assertTrue((workspace / "acct" / "notes").exists())
            with closing(sqlite3.connect(runs)) as db:
                self.assertEqual(sorted(row[0] for row in db.execute("SELECT id FROM run_sessions")), sorted([LIVE, "ephemeral:x"]))
                self.assertEqual(db.execute("SELECT run_id FROM run_events").fetchall(), [("r-live",)])
                self.assertEqual(db.execute("SELECT run_id FROM run_visible").fetchall(), [("r-live",)])
            self.assertEqual(purge(business, runs, workspace)["orphan_sessions"], 0)


if __name__ == "__main__":
    unittest.main()
