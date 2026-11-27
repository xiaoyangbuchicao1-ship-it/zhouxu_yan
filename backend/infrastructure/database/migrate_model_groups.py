"""Explicit idempotent database migration for model access groups (model_groups_v1)."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from backend.config.settings import BACKEND_ROOT

MIGRATION_ID = "model_groups_v1"
WORKSPACE_ROOT = BACKEND_ROOT.parent
DEFAULT_DB_PATH = WORKSPACE_ROOT / "data" / "relational" / "zhouxu.db"
DEFAULT_BACKUP_DIR = WORKSPACE_ROOT / "data" / "relational" / "backups"


def _tables(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def _default_backup_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return DEFAULT_BACKUP_DIR / f"zhouxu_before_model_groups_{stamp}.db"


def migrate(
    path: Path = DEFAULT_DB_PATH,
    *,
    apply: bool = True,
    backup: Path | None = None,
    no_backup: bool = False,
) -> dict:
    """Create model_access_groups and add group_id to account_capability_grants."""

    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    db = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True)
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("database_integrity_failed")
        tables = _tables(db)
        has_group_table = "model_access_groups" in tables
        has_group_col = (
            "group_id" in _columns(db, "account_capability_grants")
            if "account_capability_grants" in tables
            else True
        )
        version_recorded = (
            "schema_versions" in tables
            and db.execute(
                "SELECT 1 FROM schema_versions WHERE version=?", (MIGRATION_ID,)
            ).fetchone()
            is not None
        )
        already = has_group_table and has_group_col and version_recorded
        result: dict[str, object] = {
            "migration": MIGRATION_ID,
            "database": str(path),
            "already_migrated": already,
            "applied": False,
        }
        if already or not apply:
            return result

        if not no_backup:
            backup_path = (backup or _default_backup_path()).resolve()
            if backup_path.exists():
                raise ValueError("a_new_backup_path_is_required")
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(backup_path)) as saved:
                db.backup(saved)
                if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("backup_integrity_failed")
            result["backup"] = str(backup_path)

        db.execute("BEGIN")
        try:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS model_access_groups (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    name VARCHAR(80) NOT NULL UNIQUE,
                    description VARCHAR(240) NOT NULL DEFAULT '',
                    allowed_models_json TEXT NOT NULL DEFAULT '[]',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
            if "account_capability_grants" in tables:
                cols = _columns(db, "account_capability_grants")
                if "group_id" not in cols:
                    db.execute(
                        "ALTER TABLE account_capability_grants "
                        "ADD COLUMN group_id VARCHAR(32) REFERENCES model_access_groups(id) ON DELETE SET NULL"
                    )
                db.execute(
                    "CREATE INDEX IF NOT EXISTS ix_account_capability_grants_group_id "
                    "ON account_capability_grants (group_id)"
                )
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
        result["applied"] = True
        return result
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backup", type=Path, default=None)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    result = migrate(
        args.db,
        apply=not args.dry_run,
        backup=args.backup,
        no_backup=args.no_backup,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
