"""Explicit additive migration for the business audit table.

The API's schema initialiser can create the table for a new database.  Existing
SQLite databases must use this command while the service is stopped so the
change is backed up and inspected before it becomes live.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS business_audits (
    id VARCHAR(32) NOT NULL PRIMARY KEY,
    actor_id VARCHAR(32) NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    action VARCHAR(80) NOT NULL,
    target_account_id VARCHAR(32) REFERENCES accounts(id) ON DELETE SET NULL,
    details_json TEXT,
    created_at DATETIME NOT NULL
)
"""


def migrate(path: Path, *, apply: bool = False, backup: Path | None = None) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    if apply != (backup is not None):
        raise ValueError("apply_requires_new_backup_path")
    if backup is not None and backup.exists():
        raise ValueError("a_new_backup_path_is_required")

    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True)
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("database_integrity_failed")
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "accounts" not in tables:
            raise RuntimeError("accounts_table_missing")
        if "business_audits" in tables:
            return {
                "already_migrated": True,
                "audit_events": db.execute(
                    "SELECT COUNT(*) FROM business_audits"
                ).fetchone()[0],
                "applied": False,
            }
        result = {"already_migrated": False, "audit_events": 0, "applied": False}
        if not apply:
            return result

        backup.parent.mkdir(parents=True, exist_ok=True)
        saved = sqlite3.connect(backup)
        try:
            db.backup(saved)
            if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup_integrity_failed")
        finally:
            saved.close()
        db.execute("BEGIN")
        try:
            db.execute(CREATE_TABLE)
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_business_audits_actor_id "
                "ON business_audits (actor_id)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_business_audits_action "
                "ON business_audits (action)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_business_audits_target_account_id "
                "ON business_audits (target_account_id)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_business_audits_created_at "
                "ON business_audits (created_at)"
            )
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("migration_integrity_failed")
            db.commit()
        except BaseException:
            db.rollback()
            raise
        result["applied"] = True
        result["backup"] = str(backup)
        return result
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            migrate(args.database.resolve(), apply=args.apply, backup=args.backup),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
