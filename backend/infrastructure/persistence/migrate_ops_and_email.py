"""Explicit database migration for ops_settings & email verification tables (ops_email_v1)."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

MIGRATION_ID = "ops_email_v1"

DDL = (
    """
    CREATE TABLE IF NOT EXISTS schema_versions (
        version TEXT PRIMARY KEY,
        applied_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS system_settings (
        key VARCHAR(80) PRIMARY KEY,
        value_json TEXT NOT NULL DEFAULT '{}',
        updated_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS email_verification_codes (
        id VARCHAR(32) PRIMARY KEY,
        purpose VARCHAR(32) NOT NULL,
        email VARCHAR(320) NOT NULL,
        code_hash VARCHAR(64) NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        request_ip VARCHAR(64) NOT NULL DEFAULT '',
        expires_at DATETIME NOT NULL,
        consumed_at DATETIME,
        created_at DATETIME NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_email_codes_lookup ON email_verification_codes (email, purpose, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_email_codes_ip_recent ON email_verification_codes (request_ip, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_email_codes_expires ON email_verification_codes (expires_at)",
)


def _tables(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


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
        already = (
            "schema_versions" in tables
            and db.execute("SELECT 1 FROM schema_versions WHERE version=?", (MIGRATION_ID,)).fetchone() is not None
            and "system_settings" in tables
            and "email_verification_codes" in tables
        )
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
            for statement in DDL:
                db.execute(statement)
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
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.database, apply=args.apply, backup=args.backup), ensure_ascii=False))


if __name__ == "__main__":
    main()
