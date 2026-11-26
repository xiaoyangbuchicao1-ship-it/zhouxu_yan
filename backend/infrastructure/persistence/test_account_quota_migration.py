import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.infrastructure.persistence.migrate_account_quota import MIGRATION_ID, migrate


class AccountQuotaMigrationTests(unittest.TestCase):
    def test_adds_material_quota_mb_idempotently_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            backup_path = Path(tmp) / "backup.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE account_capability_grants (
                        account_id VARCHAR(32) PRIMARY KEY,
                        model_policy VARCHAR(24) NOT NULL DEFAULT 'default',
                        allowed_models_json TEXT,
                        skill_policy VARCHAR(24) NOT NULL DEFAULT 'default',
                        can_upload_skill INTEGER NOT NULL DEFAULT 1,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE TABLE schema_versions (version TEXT PRIMARY KEY, applied_at DATETIME NOT NULL)"
                )
                conn.commit()

            preview = migrate(db_path, apply=False)
            self.assertFalse(preview["already_migrated"])
            self.assertFalse(preview["applied"])

            applied = migrate(db_path, apply=True, backup=backup_path)
            self.assertTrue(applied["applied"])
            self.assertTrue(backup_path.is_file())

            with closing(sqlite3.connect(db_path)) as conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(account_capability_grants)")}
                self.assertIn("material_quota_mb", cols)
                rec = conn.execute(
                    "SELECT 1 FROM schema_versions WHERE version=?", (MIGRATION_ID,)
                ).fetchone()
                self.assertIsNotNone(rec)

            again = migrate(db_path, apply=False)
            self.assertTrue(again["already_migrated"])


if __name__ == "__main__":
    unittest.main()
