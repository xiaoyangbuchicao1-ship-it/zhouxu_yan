from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.infrastructure.persistence.migrate_dynamic_admin import migrate


class DynamicAdminMigrationTests(unittest.TestCase):
    def test_preflight_is_read_only_and_apply_preserves_business_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, backup = root / "source.sqlite3", root / "backup.sqlite3"
            with closing(sqlite3.connect(source)) as db:
                db.executescript(
                    """
                    PRAGMA foreign_keys=ON;
                    CREATE TABLE accounts(id TEXT PRIMARY KEY);
                    CREATE TABLE conversations(id TEXT PRIMARY KEY, account_id TEXT REFERENCES accounts(id));
                    CREATE TABLE conversation_runs(run_id TEXT PRIMARY KEY, conversation_id TEXT REFERENCES conversations(id), question TEXT, answer TEXT);
                    CREATE TABLE business_audits(id TEXT PRIMARY KEY, actor_id TEXT REFERENCES accounts(id), action TEXT, target_account_id TEXT, details_json TEXT, created_at DATETIME);
                    INSERT INTO accounts VALUES('account-1');
                    INSERT INTO conversations VALUES('conversation-1','account-1');
                    INSERT INTO conversation_runs VALUES('run-1','conversation-1','原问题','原回答');
                    """
                )
            before = source.read_bytes()
            preflight = migrate(source)
            self.assertFalse(preflight["applied"])
            self.assertEqual(source.read_bytes(), before)

            applied = migrate(source, apply=True, backup=backup)
            self.assertTrue(applied["applied"])
            self.assertTrue(backup.is_file())
            with closing(sqlite3.connect(source)) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertIn("model_provider_revisions", tables)
                self.assertIn("model_profile_revisions", tables)
                self.assertIn("model_call_metrics", tables)
                self.assertEqual(db.execute("SELECT question,answer FROM conversation_runs").fetchone(), ("原问题", "原回答"))
                columns = {row[1] for row in db.execute("PRAGMA table_info(conversation_runs)")}
                self.assertIn("model_profile_revision_id", columns)
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertIsNone(db.execute("PRAGMA foreign_key_check").fetchone())
            with closing(sqlite3.connect(backup)) as db:
                self.assertNotIn(
                    "model_providers",
                    {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                )

            again = migrate(source)
            self.assertTrue(again["already_migrated"])

    def test_apply_requires_unused_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            with closing(sqlite3.connect(source)) as db:
                db.execute("CREATE TABLE accounts(id TEXT PRIMARY KEY)")
            with self.assertRaisesRegex(ValueError, "apply_requires_new_backup_path"):
                migrate(source, apply=True)


if __name__ == "__main__":
    unittest.main()
