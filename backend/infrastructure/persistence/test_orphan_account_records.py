from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.infrastructure.persistence import (
    migrate_orphan_account_records as migration,
)
from backend.infrastructure.persistence.migrate_orphan_account_records import migrate

SCHEMA = """
CREATE TABLE accounts (id TEXT PRIMARY KEY);
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE
);
CREATE TABLE conversation_runs (
    id INTEGER PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE TABLE materials (
    id TEXT PRIMARY KEY,
    account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE
);
CREATE TABLE conversation_materials (
    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
    material_id TEXT REFERENCES materials(id) ON DELETE CASCADE
);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE
);
"""


class OrphanAccountMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Path(self.tmp.name) / "source.sqlite3"
        db = sqlite3.connect(self.database)
        try:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO accounts VALUES ('valid')")
            db.execute("INSERT INTO conversations VALUES ('orphan-conversation', 'missing')")
            db.execute("INSERT INTO conversation_runs VALUES (1, 'orphan-conversation')")
            db.execute("INSERT INTO materials VALUES ('orphan-material', 'missing')")
            db.execute(
                "INSERT INTO conversation_materials VALUES ('orphan-conversation', 'orphan-material')"
            )
            db.execute("INSERT INTO sessions VALUES ('orphan-session', 'missing')")
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_only_preflight_does_not_change_database(self):
        result = migrate(self.database)
        self.assertEqual(result["count"], 3)
        self.assertFalse(result["applied"])
        db = sqlite3.connect(self.database)
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM conversation_runs").fetchone()[0], 1)
        finally:
            db.close()

    def test_apply_removes_orphan_tree_and_keeps_valid_account(self):
        backup = Path(self.tmp.name) / "backup.sqlite3"
        result = migrate(self.database, apply=True, backup=backup)
        self.assertTrue(result["applied"])
        db = sqlite3.connect(self.database)
        try:
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1)
            for table in ("conversations", "conversation_runs", "conversation_materials", "materials", "sessions"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        finally:
            db.close()

    def test_unrelated_broken_reference_is_reported_and_never_deleted(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript(
                "CREATE TABLE projects (id TEXT PRIMARY KEY);"
                "CREATE TABLE notes (project_id TEXT REFERENCES projects(id));"
                "INSERT INTO notes VALUES ('missing-project');"
            )
            db.commit()
        backup = Path(self.tmp.name) / "backup.sqlite3"
        with self.assertRaisesRegex(RuntimeError, "unrelated_foreign_key_violation"):
            migrate(self.database, apply=True, backup=backup)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], 1)

    def test_failed_final_validation_rolls_back_the_whole_cleanup(self):
        original = migration.find_orphans
        calls = 0

        def fail_final_check(db):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("final_validation_failed")
            return original(db)

        backup = Path(self.tmp.name) / "backup.sqlite3"
        with patch.object(migration, "find_orphans", side_effect=fail_final_check):
            with self.assertRaisesRegex(RuntimeError, "final_validation_failed"):
                migrate(self.database, apply=True, backup=backup)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM conversation_runs").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM materials").fetchone()[0], 1)
        db = sqlite3.connect(backup)
        try:
            self.assertEqual(len(db.execute("PRAGMA foreign_key_check").fetchall()), 3)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
