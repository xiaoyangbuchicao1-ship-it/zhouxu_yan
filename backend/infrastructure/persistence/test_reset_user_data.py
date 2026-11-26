import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from backend.application.accounts.service import AccountService
from backend.application.workspace.conversations import WorkspaceService
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database import Database
from backend.infrastructure.persistence.reset_user_data import reset
from backend.infrastructure.persistence.run_store import SQLiteRunStore


class ResetUserDataTest(unittest.TestCase):
    def test_removes_user_data_and_keeps_accounts_and_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            business = root / "business.sqlite3"
            settings = DatabaseSettings(url=f"sqlite:///{business}")
            database = Database(settings)
            database.initialize()
            accounts = AccountService(database, settings, AuthSettings(password_min_length=8))
            owner = accounts.register(email="owner@example.com", password="a-long-owner-password")
            WorkspaceService(database).create(account_id=owner.account.id, title="旧对话", mode="chat")
            database.close()

            runs = root / "runs.sqlite3"
            SQLiteRunStore(runs).close()
            with closing(sqlite3.connect(runs)) as db:
                db.execute("INSERT INTO run_sessions VALUES ('s','{}')")
                db.execute("INSERT INTO run_events VALUES ('r',1,'{}')")
                db.execute("INSERT INTO run_visible VALUES ('r','x',1)")
                db.commit()
            files = root / "files"
            for name in ("originals", "derived", "generated"):
                (files / name / "acct" / "c").mkdir(parents=True)
                (files / name / "acct" / "c" / "f.txt").write_text("x", encoding="utf-8")
            office = root / "office.sqlite3"

            preview = reset(business, runs, office, files)
            self.assertFalse(preview["applied"])
            self.assertEqual(preview["rows"]["conversations"], 1)
            self.assertEqual(preview["kept"]["accounts"], 1)
            self.assertTrue((files / "originals" / "acct").exists(), "预览不能删任何东西")

            with self.assertRaises(ValueError):
                reset(business, runs, office, files, apply=True)

            done = reset(business, runs, office, files, apply=True, confirm_wipe=True)
            self.assertTrue(done["applied"])
            after = reset(business, runs, office, files)
            self.assertTrue(all((count or 0) == 0 for count in after["rows"].values()), after["rows"])
            self.assertEqual(after["kept"]["accounts"], 1)
            self.assertTrue(all(size == 0 for size in after["files"].values()))
            for name in ("originals", "derived", "generated"):
                self.assertTrue((files / name).is_dir(), "目录本身保留")
            with closing(sqlite3.connect(business)) as db:
                self.assertEqual(db.execute("SELECT email FROM accounts").fetchone()[0], "owner@example.com")


if __name__ == "__main__":
    unittest.main()
