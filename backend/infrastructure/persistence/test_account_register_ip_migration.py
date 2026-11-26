"""Unit tests for account_register_ip_v1 migration."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.infrastructure.persistence.migrate_account_register_ip import (
    MIGRATION_ID,
    _columns,
    migrate,
)


class AccountRegisterIpMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.backup_path = Path(self.temp_dir.name) / "backup.db"
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                CREATE TABLE accounts (
                    id VARCHAR(32) PRIMARY KEY,
                    email VARCHAR(320) UNIQUE,
                    display_name VARCHAR(80),
                    password_hash TEXT,
                    status VARCHAR(24),
                    role VARCHAR(16),
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
            db.execute(
                """
                CREATE TABLE schema_versions (
                    version TEXT PRIMARY KEY,
                    applied_at DATETIME
                )
                """
            )
            db.execute(
                "INSERT INTO accounts VALUES ('acc1', 'user@example.com', 'User', 'hash', 'active', 'user', '2026-11-20 00:00:00', '2026-11-20 00:00:00')"
            )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dry_run_does_not_modify(self):
        res = migrate(self.db_path, apply=False, backup=None)
        self.assertFalse(res["already_migrated"])
        self.assertFalse(res["applied"])
        with sqlite3.connect(self.db_path) as db:
            self.assertNotIn("register_ip", _columns(db, "accounts"))

    def test_apply_migrates_column_and_records_version(self):
        res = migrate(self.db_path, apply=True, backup=self.backup_path)
        self.assertTrue(res["applied"])
        self.assertTrue(self.backup_path.exists())

        with sqlite3.connect(self.db_path) as db:
            self.assertIn("register_ip", _columns(db, "accounts"))
            row = db.execute("SELECT register_ip FROM accounts WHERE id='acc1'").fetchone()
            self.assertIsNone(row[0])
            version = db.execute("SELECT version FROM schema_versions WHERE version=?", (MIGRATION_ID,)).fetchone()
            self.assertIsNotNone(version)

        # Idempotent re-run
        backup2 = Path(self.temp_dir.name) / "backup2.db"
        res2 = migrate(self.db_path, apply=False, backup=None)
        self.assertTrue(res2["already_migrated"])


if __name__ == "__main__":
    unittest.main()
