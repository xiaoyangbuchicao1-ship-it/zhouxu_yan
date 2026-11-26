"""Explicit database migration for account register_ip column (account_register_ip_v1)."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

MIGRATION_ID = "account_register_ip_v1"


def _tables(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def migrate(path: Path, *, apply: bool = False, backup: Path | None = None) -> dict:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if apply != (backup is not None):
        raise ValueError("apply_requires_new_backup_path")
    if backup is not None and backup.exists():
        raise ValueError("a_new_backup_path_is_required")

    db = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True)
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("database_integrity_failed")
        tables = _tables(db)
        already = False
        if "accounts" in tables and "schema_versions" in tables:
            cols = _columns(db, "accounts")
            version_recorded = (
                db.execute(
                    "SELECT 1 FROM schema_versions WHERE version=?", (MIGRATION_ID,)
                ).fetchone()
                is not None
            )
            already = "register_ip" in cols and version_recorded

        result = {"migration": MIGRATION_ID, "already_migrated": already, "applied": False}
        if already or not apply:
            return result

        assert backup is not None
        backup = backup.resolve()
        backup.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(backup)) as saved:
            db.backup(saved)
            if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup_integrity_failed")

        db.execute("BEGIN")
        try:
            if "accounts" in tables:
                cols = _columns(db, "accounts")
                if "register_ip" not in cols:
                    db.execute("ALTER TABLE accounts ADD COLUMN register_ip VARCHAR(64)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS schema_versions (version TEXT PRIMARY KEY, applied_at DATETIME NOT NULL)"
            )
            db.execute(
                "INSERT OR IGNORE INTO schema_versions(version, applied_at) VALUES (?, datetime('now'))",
                (MIGRATION_ID,),
            )
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("migration_integrity_failed")
            db.commit()
        except BaseException:
            db.rollback()
            raise
        result.update({"applied": True, "backup": str(backup)})
        return result
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    result = migrate(args.database, apply=args.apply, backup=args.backup)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
