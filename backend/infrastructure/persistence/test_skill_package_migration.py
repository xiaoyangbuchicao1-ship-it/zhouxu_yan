import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.application.maritime.service import MaritimeOffice
from backend.infrastructure.persistence.migrate_skill_packages import migrate


class SkillPackageMigrationTests(unittest.TestCase):
    def test_incomplete_schema_is_reported_before_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "office.sqlite3"
            MaritimeOffice(path)
            with sqlite3.connect(path) as db:
                db.execute("DROP TABLE skill_installations")
            with self.assertRaisesRegex(RuntimeError, "skill_package_schema_incomplete"):
                migrate(path)

    def test_explicit_migration_preserves_old_skills_and_leaves_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "office.sqlite3"
            office = MaritimeOffice(path)
            original = {
                "id": "legacy-skill", "owner_id": "owner", "name": "旧技能", "summary": "保留原内容",
                "instructions": "原始方法", "version": 1, "visibility": "private", "parent_id": None,
                "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
            }
            with office.connection() as db:
                db.execute("INSERT INTO skills VALUES (:id,:owner_id,:name,:summary,:instructions,:version,:visibility,:parent_id,:created_at,:updated_at)", original)
            with sqlite3.connect(path) as db:
                db.executescript("DROP TABLE skill_package_files; DROP TABLE skill_packages; DROP TABLE skill_installations;")
            with self.assertRaisesRegex(RuntimeError, "skill_package_migration_required"):
                MaritimeOffice(path)
            check = migrate(path)
            self.assertFalse(check["applied"])
            backup = Path(tmp) / "backup.sqlite3"
            applied = migrate(path, apply=True, backup=backup)
            self.assertTrue(applied["applied"])
            self.assertEqual(check["original_sha256"], applied["original_sha256"])
            self.assertEqual(MaritimeOffice(path).skill("owner", original["id"]), original)
            with sqlite3.connect(backup) as db:
                self.assertEqual(db.execute("SELECT instructions FROM skills").fetchone()[0], "原始方法")
            self.assertTrue(migrate(path, apply=True, backup=backup)["already_migrated"])


if __name__ == "__main__":
    unittest.main()
