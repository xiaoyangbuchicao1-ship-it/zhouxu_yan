import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.application.accounts.service import AccountService
from backend.application.workspace.conversations import WorkspaceService
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database import Database
from backend.infrastructure.persistence.migrate_conversation_recency import migrate


class ConversationRecencyMigrationTest(unittest.TestCase):
    def test_restart_inflated_times_are_repaired_and_nothing_else_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "business.sqlite3"
            settings = DatabaseSettings(url=f"sqlite:///{path}")
            database = Database(settings)
            database.initialize()
            accounts = AccountService(database, settings, AuthSettings(password_min_length=8))
            owner = accounts.register(email="owner@example.com", password="a-long-owner-password").account
            workspace = WorkspaceService(database)
            old = workspace.create(account_id=owner.id, title="旧对话", mode="chat")
            idle = workspace.create(account_id=owner.id, title="没有任务的对话", mode="chat")
            when = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
            workspace.record_run(
                account_id=owner.id, conversation_id=old.id, run_id="r1", question="问题",
                status="succeeded", answer="答案", created_at=when, completed_at=when + timedelta(minutes=2),
            )
            database.close()

            with closing(sqlite3.connect(path)) as db:
                # The conversations were created before their Runs; then repeated
                # restarts made updated_at jump to "now".
                db.execute("UPDATE conversations SET created_at='2026-10-31 08:00:00.000000'")
                db.execute("UPDATE conversations SET updated_at='2026-11-24 09:00:00.000000'")
                db.commit()
                idle_before = db.execute("SELECT updated_at FROM conversations WHERE id=?", (idle.id,)).fetchone()[0]

            preflight = migrate(path)
            self.assertEqual(preflight["to_repair"], 1)
            self.assertFalse(preflight["applied"])
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(
                    db.execute("SELECT updated_at FROM conversations WHERE id=?", (old.id,)).fetchone()[0],
                    "2026-11-24 09:00:00.000000",
                )

            with self.assertRaises(ValueError):
                migrate(path, apply=True)
            backup = Path(tmp) / "backup.sqlite3"
            applied = migrate(path, apply=True, backup=backup)
            self.assertTrue(applied["applied"])
            with closing(sqlite3.connect(path)) as db:
                repaired = db.execute("SELECT updated_at FROM conversations WHERE id=?", (old.id,)).fetchone()[0]
                self.assertTrue(str(repaired).startswith("2026-11-01 08:02"), repaired)
                self.assertEqual(db.execute("SELECT updated_at FROM conversations WHERE id=?", (idle.id,)).fetchone()[0], idle_before)
            with closing(sqlite3.connect(backup)) as saved:
                self.assertEqual(
                    saved.execute("SELECT updated_at FROM conversations WHERE id=?", (old.id,)).fetchone()[0],
                    "2026-11-24 09:00:00.000000",
                )
            self.assertEqual(migrate(path)["to_repair"], 0)


if __name__ == "__main__":
    unittest.main()
