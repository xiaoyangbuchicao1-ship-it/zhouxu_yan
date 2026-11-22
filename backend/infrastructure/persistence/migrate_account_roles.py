"""Explicit additive migration for account roles.

Run this while the API is stopped.  The default is a read-only preflight;
applying the migration requires a new SQLite backup path.  Existing account
rows and all unrelated tables are preserved byte-for-byte at the logical row
level.  Legacy accounts receive the ordinary ``user`` role.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def _table_digest(
    db: sqlite3.Connection, table: str, *, exclude: set[str] | None = None
) -> str:
    excluded = exclude or set()
    columns = [
        row[1]
        for row in db.execute(f'PRAGMA table_info("{table}")')
        if row[1] not in excluded
    ]
    digest = hashlib.sha256()
    selected = ", ".join(f'"{column}"' for column in columns)
    for row in db.execute(f'SELECT {selected} FROM "{table}" ORDER BY rowid'):
        digest.update(
            json.dumps(
                dict(zip(columns, row, strict=True)),
                ensure_ascii=False,
                default=lambda value: value.hex() if isinstance(value, bytes) else str(value),
                sort_keys=True,
            ).encode("utf-8")
        )
    return digest.hexdigest()


def migrate(path: Path, *, apply: bool = False, backup: Path | None = None) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    if apply != (backup is not None):
        raise ValueError("apply_requires_new_backup_path")
    if backup is not None and backup.exists():
        raise ValueError("a_new_backup_path_is_required")

    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("database_integrity_failed")
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "accounts" not in tables:
            raise RuntimeError("accounts_table_missing")
        columns = {
            row[1] for row in db.execute("PRAGMA table_info(accounts)")
        }
        if "role" in columns:
            values = {
                row[0]
                for row in db.execute("SELECT DISTINCT role FROM accounts")
            }
            if not values.issubset({"user", "admin"}):
                raise RuntimeError("invalid_account_role")
            return {
                "already_migrated": True,
                "accounts": db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
                "applied": False,
            }

        before = _table_digest(db, "accounts")
        result = {
            "already_migrated": False,
            "accounts": db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
            "applied": False,
        }
        if not apply:
            return result

        backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(backup) as saved:
            db.backup(saved)
            if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup_integrity_failed")
        db.execute(
            "ALTER TABLE accounts ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'user'"
        )
        db.commit()
        if _table_digest(db, "accounts", exclude={"role"}) != before:
            raise RuntimeError("account_role_migration_incomplete")
        if db.execute("SELECT COUNT(*) FROM accounts WHERE role != 'user'").fetchone()[0]:
            raise RuntimeError("account_role_migration_invalid_default")
        result["applied"] = True
        result["backup"] = str(backup)
        return result


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
