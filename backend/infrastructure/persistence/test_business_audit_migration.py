from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.infrastructure.persistence import migrate_business_audit as migration


class BusinessAuditMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Path(self.tmp.name) / "source.sqlite3"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY)")
            db.execute("INSERT INTO accounts VALUES ('valid')")
            db.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_explicit_migration_preserves_accounts_and_backup(self):
        preview = migration.migrate(self.database)
        self.assertFalse(preview["applied"])
        backup = Path(self.tmp.name) / "backup.sqlite3"
        result = migration.migrate(self.database, apply=True, backup=backup)
        self.assertTrue(result["applied"])
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM business_audits").fetchone()[0], 0)
        with closing(sqlite3.connect(backup)) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='business_audits'").fetchone())

    def test_failed_schema_change_rolls_back_table_and_indexes(self):
        original_connect = sqlite3.connect

        class FailingConnection(sqlite3.Connection):
            def execute(self, statement, *args, **kwargs):
                if "CREATE INDEX IF NOT EXISTS ix_business_audits_created_at" in statement:
                    raise sqlite3.OperationalError("simulated_index_failure")
                return super().execute(statement, *args, **kwargs)

        def connect(path, *args, **kwargs):
            if str(path).startswith("file:"):
                kwargs["factory"] = FailingConnection
            return original_connect(path, *args, **kwargs)

        with patch.object(migration.sqlite3, "connect", side_effect=connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, "simulated_index_failure"):
                migration.migrate(self.database, apply=True, backup=Path(self.tmp.name) / "backup.sqlite3")
        with closing(original_connect(self.database)) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='business_audits'").fetchone())

    def test_migration_closes_source_and_backup_connections(self):
        original_connect = sqlite3.connect
        opened = []

        def connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        with patch.object(migration.sqlite3, "connect", side_effect=connect):
            migration.migrate(self.database, apply=True, backup=Path(self.tmp.name) / "backup.sqlite3")
        for connection in opened:
            try:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
