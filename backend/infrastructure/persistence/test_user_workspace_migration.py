from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.config.settings import DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.infrastructure.persistence.migrate_user_workspace import MIGRATION_ID, migrate


class UserWorkspaceMigrationTests(unittest.TestCase):
    def test_preflight_is_read_only_and_apply_creates_tables_without_losing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, backup = root / "source.sqlite3", root / "backup.sqlite3"
            with closing(sqlite3.connect(source)) as db:
                db.executescript(
                    """
                    PRAGMA foreign_keys=ON;
                    CREATE TABLE schema_versions(version TEXT PRIMARY KEY, applied_at DATETIME NOT NULL);
                    INSERT INTO schema_versions VALUES('dynamic_admin_v3', '2026-11-22 00:00:00');
                    CREATE TABLE accounts(id TEXT PRIMARY KEY, email TEXT);
                    CREATE TABLE conversations(id TEXT PRIMARY KEY, account_id TEXT REFERENCES accounts(id));
                    INSERT INTO accounts VALUES('acc-1', 'user@example.com');
                    INSERT INTO conversations VALUES('conv-1', 'acc-1');
                    """
                )
            before = source.read_bytes()
            preflight = migrate(source)
            self.assertFalse(preflight["applied"])
            self.assertFalse(preflight["already_migrated"])
            self.assertEqual(source.read_bytes(), before)

            applied = migrate(source, apply=True, backup=backup)
            self.assertTrue(applied["applied"])
            self.assertTrue(backup.is_file())
            with closing(sqlite3.connect(source)) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertIn("user_preferences", tables)
                self.assertIn("user_prompt_commands", tables)
                self.assertEqual(db.execute("SELECT id, account_id FROM conversations").fetchall(), [("conv-1", "acc-1")])
                versions = {row[0] for row in db.execute("SELECT version FROM schema_versions")}
                self.assertIn(MIGRATION_ID, versions)
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertIsNone(db.execute("PRAGMA foreign_key_check").fetchone())

            again = migrate(source)
            self.assertTrue(again["already_migrated"])

    def test_runtime_gate_blocks_existing_db_missing_user_workspace_v1(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v3_only.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                db.executescript(
                    """
                    CREATE TABLE schema_versions(version TEXT PRIMARY KEY, applied_at DATETIME NOT NULL);
                    INSERT INTO schema_versions VALUES('dynamic_admin_v3', '2026-11-22 00:00:00');
                    CREATE TABLE accounts(id TEXT PRIMARY KEY);
                    """
                )
            database = Database(DatabaseSettings(url=f"sqlite:///{path}"))
            try:
                with self.assertRaisesRegex(RuntimeError, "explicit_migration_required"):
                    database.initialize(require_explicit_migrations=True)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
