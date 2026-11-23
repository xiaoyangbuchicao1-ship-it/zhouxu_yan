"""Explicit migration for user preferences and custom slash prompt commands.

Read-only by default.  Applying changes requires a fresh backup path so
existing relational accounts, conversations, runs, and model directories can be
recovered without loss.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

MIGRATION_ID = "user_workspace_v1"

DDL = (
    """
    CREATE TABLE IF NOT EXISTS schema_versions (
        version TEXT PRIMARY KEY,
        applied_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_preferences (
        account_id VARCHAR(32) PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
        default_mode VARCHAR(16) NOT NULL DEFAULT 'chat',
        default_thinking_mode VARCHAR(16) NOT NULL DEFAULT 'none',
        concise_process BOOLEAN NOT NULL DEFAULT 0,
        compact_density BOOLEAN NOT NULL DEFAULT 0,
        reduced_motion BOOLEAN NOT NULL DEFAULT 0,
        memory_enabled BOOLEAN NOT NULL DEFAULT 0,
        memory_text TEXT NOT NULL DEFAULT '',
        commands_seeded BOOLEAN NOT NULL DEFAULT 0,
        updated_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_prompt_commands (
        id VARCHAR(32) PRIMARY KEY,
        account_id VARCHAR(32) NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        trigger VARCHAR(48) NOT NULL,
        title VARCHAR(80) NOT NULL,
        content TEXT NOT NULL,
        mode_hint VARCHAR(16),
        is_builtin BOOLEAN NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        UNIQUE(account_id, trigger)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_user_prompt_commands_account_id
    ON user_prompt_commands (account_id)
    """,
)


def _tables(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}


def _counts(db: sqlite3.Connection) -> dict[str, int]:
    result = {}
    for table in ("accounts", "conversations", "conversation_runs", "user_preferences", "user_prompt_commands"):
        if table in _tables(db):
            result[table] = int(db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    return result


def migrate(path: Path, *, apply: bool = False, backup: Path | None = None) -> dict:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if apply != (backup is not None):
        raise ValueError("apply_requires_new_backup_path")
    if backup is not None and backup.exists():
        raise ValueError("a_new_backup_path_is_required")

    with closing(sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("database_integrity_failed")
        tables = _tables(db)
        if "accounts" not in tables:
            raise RuntimeError("accounts_table_missing")
        already = (
            "schema_versions" in tables
            and db.execute(
                "SELECT 1 FROM schema_versions WHERE version=?", (MIGRATION_ID,)
            ).fetchone()
            is not None
        )
        result = {"already_migrated": already, "applied": False, "before": _counts(db)}
        if already:
            return result
        if not apply:
            result["after"] = dict(result["before"])
            return result

        backup = backup.resolve()
        backup.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(backup)) as dst:
            db.backup(dst)

        db.execute("PRAGMA foreign_keys=ON")
        with db:
            for statement in DDL:
                db.execute(statement)
            if "user_preferences" in _tables(db) and "commands_seeded" not in _columns(db, "user_preferences"):
                db.execute(
                    "ALTER TABLE user_preferences ADD COLUMN commands_seeded BOOLEAN NOT NULL DEFAULT 0"
                )
            db.execute(
                "INSERT OR IGNORE INTO schema_versions(version, applied_at) VALUES (?, datetime('now'))",
                (MIGRATION_ID,),
            )

        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("database_integrity_failed_after_migration")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("foreign_key_violation_after_migration")

        result["applied"] = True
        result["backup"] = str(backup)
        result["after"] = _counts(db)
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Explicit SQLite migration for user_workspace_v1")
    parser.add_argument("path", type=Path, help="Path to zhouxu.db")
    parser.add_argument("--apply", action="store_true", help="Apply migration in place after backing up")
    parser.add_argument("--backup", type=Path, default=None, help="New backup SQLite file path")
    args = parser.parse_args()
    summary = migrate(args.path, apply=args.apply, backup=args.backup)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
