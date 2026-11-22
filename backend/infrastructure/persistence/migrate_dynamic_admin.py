"""Explicit migration for the dynamic model directory and admin log fields.

The command is read-only by default.  Applying it requires a new backup path
and must be done while the API is stopped.  It never deletes rows or silently
converts the old payload projection.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path


MIGRATION_ID = "dynamic_admin_v1"


DDL = (
    """
    CREATE TABLE IF NOT EXISTS schema_versions (
        version TEXT PRIMARY KEY,
        applied_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS account_capability_grants (
        account_id VARCHAR(32) PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
        model_policy VARCHAR(24) NOT NULL DEFAULT 'default',
        allowed_models_json TEXT,
        skill_policy VARCHAR(24) NOT NULL DEFAULT 'default',
        can_upload_skill BOOLEAN NOT NULL DEFAULT 1,
        updated_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_providers (
        id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        preset_type VARCHAR(32) NOT NULL DEFAULT 'custom',
        default_protocol VARCHAR(64) NOT NULL,
        auth_style VARCHAR(32) NOT NULL DEFAULT 'bearer',
        base_url TEXT NOT NULL,
        extra_headers_json TEXT,
        credential_mode VARCHAR(24) NOT NULL DEFAULT 'env_ref',
        credential_ref VARCHAR(120),
        api_key_encrypted TEXT,
        api_key_hint VARCHAR(32),
        enabled BOOLEAN NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        archived_at DATETIME
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_provider_revisions (
        id VARCHAR(80) PRIMARY KEY,
        provider_id VARCHAR(64) NOT NULL REFERENCES model_providers(id) ON DELETE CASCADE,
        revision INTEGER NOT NULL,
        default_protocol VARCHAR(64) NOT NULL,
        auth_style VARCHAR(32) NOT NULL DEFAULT 'bearer',
        base_url TEXT NOT NULL,
        credential_mode VARCHAR(24) NOT NULL DEFAULT 'env_ref',
        credential_ref VARCHAR(120),
        config_json TEXT,
        created_at DATETIME NOT NULL,
        UNIQUE(provider_id, revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_profiles (
        profile_id VARCHAR(160) PRIMARY KEY,
        provider_id VARCHAR(64) NOT NULL REFERENCES model_providers(id) ON DELETE CASCADE,
        upstream_model_id VARCHAR(200) NOT NULL,
        label VARCHAR(160) NOT NULL,
        family VARCHAR(32) NOT NULL,
        protocol_override VARCHAR(64),
        context_tokens INTEGER NOT NULL,
        max_output_tokens INTEGER NOT NULL,
        thinking_modes_json TEXT NOT NULL DEFAULT '["none"]',
        default_thinking VARCHAR(16) NOT NULL DEFAULT 'none',
        capabilities_json TEXT,
        enabled BOOLEAN NOT NULL DEFAULT 1,
        access_scope VARCHAR(24) NOT NULL DEFAULT 'all',
        is_default BOOLEAN NOT NULL DEFAULT 0,
        sort_order INTEGER NOT NULL DEFAULT 100,
        verification_json TEXT,
        current_revision INTEGER NOT NULL DEFAULT 1,
        source VARCHAR(120) NOT NULL DEFAULT 'admin',
        checked_on VARCHAR(40),
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_profile_revisions (
        id VARCHAR(220) PRIMARY KEY,
        profile_id VARCHAR(160) NOT NULL REFERENCES model_profiles(profile_id) ON DELETE CASCADE,
        provider_revision_id VARCHAR(80) NOT NULL REFERENCES model_provider_revisions(id) ON DELETE RESTRICT,
        revision INTEGER NOT NULL,
        facts_json TEXT NOT NULL,
        created_at DATETIME NOT NULL,
        UNIQUE(profile_id, revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_call_metrics (
        id VARCHAR(32) PRIMARY KEY,
        run_id VARCHAR(64),
        provider_id VARCHAR(64),
        provider_revision_id VARCHAR(80),
        profile_id VARCHAR(160),
        profile_revision_id VARCHAR(220),
        protocol VARCHAR(64),
        status VARCHAR(24) NOT NULL,
        latency_ms INTEGER,
        input_tokens INTEGER,
        output_tokens INTEGER,
        error_code VARCHAR(80),
        created_at DATETIME NOT NULL
    )
    """,
)


def _tables(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}


def _additive_columns(db: sqlite3.Connection) -> None:
    if "business_audits" in _tables(db):
        existing = _columns(db, "business_audits")
        additions = {
            "resource_type": "VARCHAR(40) NOT NULL DEFAULT 'account'",
            "resource_id": "VARCHAR(160)",
            "outcome": "VARCHAR(24) NOT NULL DEFAULT 'succeeded'",
        }
        for name, declaration in additions.items():
            if name not in existing:
                db.execute(f'ALTER TABLE business_audits ADD COLUMN "{name}" {declaration}')
    if "conversation_runs" in _tables(db):
        existing = _columns(db, "conversation_runs")
        additions = {
            "model_profile_id": "VARCHAR(160)",
            "model_profile_revision_id": "VARCHAR(220)",
            "provider_revision_id": "VARCHAR(80)",
            "model_snapshot_json": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in existing:
                db.execute(f'ALTER TABLE conversation_runs ADD COLUMN "{name}" {declaration}')


def _counts(db: sqlite3.Connection) -> dict[str, int]:
    result = {}
    for table in ("accounts", "conversations", "conversation_runs", "model_providers", "model_profiles", "model_call_metrics"):
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

    db = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True)
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("database_integrity_failed")
        if "accounts" not in _tables(db):
            raise RuntimeError("accounts_table_missing")
        existing_versions = _tables(db)
        already = "schema_versions" in existing_versions and db.execute(
            "SELECT 1 FROM schema_versions WHERE version=?", (MIGRATION_ID,)
        ).fetchone() is not None
        result = {"already_migrated": already, "applied": False, "before": _counts(db)}
        if already:
            return result
        if not apply:
            result["after"] = dict(result["before"])
            return result

        assert backup is not None
        backup = backup.resolve()
        backup.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(backup)) as saved:
            db.backup(saved)
            if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup_integrity_failed")

        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN")
        try:
            for statement in DDL:
                db.execute(statement)
            _additive_columns(db)
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_model_profiles_provider_id ON model_profiles(provider_id)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_model_profiles_enabled ON model_profiles(enabled, access_scope)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_model_call_metrics_created_at ON model_call_metrics(created_at)"
            )
            db.execute(
                "INSERT INTO schema_versions(version, applied_at) VALUES (?, datetime('now'))",
                (MIGRATION_ID,),
            )
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("migration_integrity_failed")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("migration_foreign_key_failed")
            db.commit()
        except BaseException:
            db.rollback()
            raise
        result.update({"applied": True, "backup": str(backup), "after": _counts(db)})
        return result
    finally:
        db.close()


def seed_static_catalog(path: Path) -> dict[str, int]:
    """Explicitly copy the checked-in model facts into an already migrated DB."""

    from backend.config.settings import load_settings
    from backend.infrastructure.database.connection import Database
    from backend.infrastructure.model.sqlite_directory import SqliteModelDirectory

    database = Database(f"sqlite:///{path.resolve().as_posix()}")
    try:
        directory = SqliteModelDirectory(database, load_settings())
        directory.ensure_seed(explicit=True)
        with database.session() as session:
            from backend.infrastructure.database.models import ModelProfileModel

            count = len(session.query(ModelProfileModel.profile_id).all())
        return {"model_profiles": count}
    finally:
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--seed", action="store_true", help="seed the checked-in model catalog after migration")
    args = parser.parse_args()
    result = migrate(args.database, apply=args.apply, backup=args.backup)
    if args.seed:
        result["seed"] = seed_static_catalog(args.database)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
