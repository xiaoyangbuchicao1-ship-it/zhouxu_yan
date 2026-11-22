"""Remove rows that point to accounts no longer present in the account table.

The command is read-only by default.  Applying it requires a new SQLite
backup path and deletes only the exact child rows reported by
``PRAGMA foreign_key_check``.  It never deletes accounts, files, or rows with
valid account references.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def find_orphans(db: sqlite3.Connection) -> list[dict[str, object]]:
    rows = db.execute("PRAGMA foreign_key_check").fetchall()
    orphans = [
        {"table": row[0], "rowid": row[1], "parent": row[2], "foreign_key": row[3]}
        for row in rows
    ]
    allowed_tables = {"conversations", "materials", "sessions"}
    unrelated = [
        item
        for item in orphans
        if item["table"] not in allowed_tables or item["parent"] != "accounts"
    ]
    if unrelated:
        raise RuntimeError("unrelated_foreign_key_violation")
    return orphans


def migrate(path: Path, *, apply: bool = False, backup: Path | None = None) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    if apply != (backup is not None):
        raise ValueError("apply_requires_new_backup_path")
    if backup is not None and backup.exists():
        raise ValueError("a_new_backup_path_is_required")
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True)
    try:
        # The account rows are the root of several account-owned trees.  Turn
        # on SQLite foreign-key enforcement before deleting the exact rows
        # reported by foreign_key_check so declared ON DELETE CASCADE rules
        # remove their dependent rows in the same transaction.
        db.execute("PRAGMA foreign_keys=ON")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("database_integrity_failed")
        orphans = find_orphans(db)
        result = {"orphans": orphans, "count": len(orphans), "applied": False}
        if not apply or not orphans:
            return result
        backup.parent.mkdir(parents=True, exist_ok=True)
        saved = sqlite3.connect(backup)
        try:
            db.backup(saved)
            if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup_integrity_failed")
        finally:
            saved.close()
        db.execute("BEGIN IMMEDIATE")
        try:
            for item in orphans:
                table = str(item["table"])
                db.execute(f'DELETE FROM "{table}" WHERE rowid=?', (item["rowid"],))
            remaining = find_orphans(db)
            if remaining:
                raise RuntimeError("orphan_account_migration_incomplete")
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
