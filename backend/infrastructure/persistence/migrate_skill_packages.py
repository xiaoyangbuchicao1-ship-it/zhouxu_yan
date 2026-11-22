"""Explicit additive migration. Run with the API stopped in the Linux volume.

Only new package tables are added; existing skills, versions, runs, events and
business projections keep their contents. Backup is mandatory before apply.
"""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from backend.skills.packages import create_package_schema


def migrate(path: Path, *, apply: bool = False, backup: Path | None = None) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("office_database_integrity_failed")
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        digest = hashlib.sha256()
        counts = {}
        for name in tables:
            if not name.replace("_", "").isalnum():
                raise RuntimeError("unexpected_table_name")
            rows = db.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
            counts[name] = len(rows)
            digest.update(json.dumps([name, rows], ensure_ascii=False, default=str).encode())
        package_tables = {"skill_packages", "skill_package_files", "skill_installations"}
        present_package_tables = package_tables.intersection(tables)
        if present_package_tables and present_package_tables != package_tables:
            raise RuntimeError("skill_package_schema_incomplete")
        result = {"already_migrated": present_package_tables == package_tables, "tables": counts, "original_sha256": digest.hexdigest(), "applied": False}
        if not apply or result["already_migrated"]:
            return result
        if backup is None or backup.exists():
            raise ValueError("a_new_backup_path_is_required")
        backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(backup) as saved:
            db.backup(saved)
            if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("office_backup_integrity_failed")
        create_package_schema(db)
        after = hashlib.sha256()
        for name in tables:
            after.update(json.dumps([name, db.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()], ensure_ascii=False, default=str).encode())
        if digest.hexdigest() != after.hexdigest():
            raise RuntimeError("office_existing_data_changed")
        result["applied"] = True
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.database.resolve(), apply=args.apply, backup=args.backup), ensure_ascii=False))
