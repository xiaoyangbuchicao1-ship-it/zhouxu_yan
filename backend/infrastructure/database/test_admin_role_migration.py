"""Red test for the explicit additive account-role migration."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.config.settings import DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.infrastructure.persistence.migrate_account_roles import migrate


class AdminRoleMigrationTests(unittest.TestCase):
    def test_initialize_adds_role_to_legacy_accounts_without_dropping_rows(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            path = Path(directory) / "legacy.sqlite3"
            with sqlite3.connect(path) as database:
                database.execute(
                    """
                    CREATE TABLE accounts (
                        id VARCHAR(32) PRIMARY KEY,
                        email VARCHAR(320) UNIQUE NOT NULL,
                        display_name VARCHAR(80) NOT NULL,
                        password_hash TEXT NOT NULL,
                        status VARCHAR(24) NOT NULL,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
                database.execute(
                    """
                    INSERT INTO accounts
                        (id, email, display_name, password_hash, status, created_at, updated_at)
                    VALUES ('legacy-1', 'legacy@example.com', 'Legacy', 'hash', 'active',
                            '2026-01-01T00:00:00', '2026-01-01T00:00:00')
                    """
                )
                database.commit()

            adapter = Database(DatabaseSettings(url=f"sqlite:///{path}"))
            adapter.initialize()
            adapter.close()
            with sqlite3.connect(path) as database:
                columns = {
                    column[1]
                    for column in database.execute("PRAGMA table_info(accounts)")
                }
                row = database.execute(
                    "SELECT id, email, role FROM accounts WHERE id = 'legacy-1'"
                ).fetchone()

            self.assertIn("role", columns)
            self.assertEqual(row[0], "legacy-1")
            self.assertEqual(row[1], "legacy@example.com")
            self.assertEqual(row[2], "user")

    def test_explicit_migration_requires_backup_and_preserves_legacy_rows(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            path = root / "legacy.sqlite3"
            with sqlite3.connect(path) as database:
                database.execute(
                    "CREATE TABLE accounts (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, "
                    "display_name TEXT NOT NULL, password_hash TEXT NOT NULL, status TEXT NOT NULL, "
                    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
                )
                database.execute(
                    "INSERT INTO accounts VALUES ('legacy-1', 'legacy@example.com', 'Legacy', 'hash', 'active', 'now', 'now')"
                )
            self.assertEqual(migrate(path), {"already_migrated": False, "accounts": 1, "applied": False})
            backup = root / "backup.sqlite3"
            result = migrate(path, apply=True, backup=backup)
            self.assertTrue(result["applied"])
            with sqlite3.connect(path) as database:
                self.assertEqual(
                    database.execute("SELECT id, email, role FROM accounts").fetchone(),
                    ("legacy-1", "legacy@example.com", "user"),
                )
            with sqlite3.connect(backup) as database:
                self.assertEqual(database.execute("SELECT id, email FROM accounts").fetchone(), ("legacy-1", "legacy@example.com"))


if __name__ == "__main__":
    unittest.main()
