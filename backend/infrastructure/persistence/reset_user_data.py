"""Explicit reset of everything users created, keeping accounts and configuration.

Run with the API stopped.  Without ``--apply`` it only prints what would go.

Kept:   accounts (email, password hash, role, status), per-account capability
        grants, model providers/models and their encrypted keys, schema
        versions, and the searchable corpus (laws, cases, papers).
Removed: conversations, Runs and their events, materials and every file made
        from them, projects, custom commands, memory, sign-in sessions, audit
        records and uploaded skills.

The owner decided that deletions need no backup.  Applying also requires
``--confirm-wipe`` so a stray ``--apply`` cannot empty the system.
"""

import argparse
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

# Children first, so foreign keys never block a delete.
BUSINESS_TABLES = (
    "conversation_materials",
    "conversation_runs",
    "materials",
    "conversations",
    "projects",
    "harness_sessions",
    "model_call_metrics",
    "user_prompt_commands",
    "user_preferences",
    "sessions",
    "business_audits",
)
RUN_STORE_TABLES = ("run_events", "run_visible", "run_sessions")
OFFICE_TABLES = (
    "skill_package_files",
    "skill_packages",
    "skill_installations",
    "skill_versions",
    "skills",
)
FILE_DIRECTORIES = ("originals", "derived", "generated")


def _count(db: sqlite3.Connection, table: str) -> int | None:
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if exists else None


def _size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) if path.is_dir() else 0


def reset(
    business_db: Path,
    runs_db: Path,
    office_db: Path,
    files_root: Path,
    *,
    apply: bool = False,
    confirm_wipe: bool = False,
) -> dict:
    plan: dict = {"rows": {}, "kept": {}, "files": {}, "applied": False}
    with closing(sqlite3.connect(business_db, timeout=30)) as db:
        for table in BUSINESS_TABLES:
            plan["rows"][table] = _count(db, table)
        for table in ("accounts", "account_capability_grants", "model_providers", "model_profiles"):
            plan["kept"][table] = _count(db, table)
    with closing(sqlite3.connect(runs_db, timeout=30)) as db:
        for table in RUN_STORE_TABLES:
            plan["rows"][table] = _count(db, table)
    if office_db.is_file():
        with closing(sqlite3.connect(office_db, timeout=30)) as db:
            for table in OFFICE_TABLES:
                plan["rows"][table] = _count(db, table)
    for name in FILE_DIRECTORIES:
        plan["files"][name] = _size(files_root / name)
    if not apply:
        return plan
    if not confirm_wipe:
        raise ValueError("confirm_wipe_required")

    with closing(sqlite3.connect(business_db, timeout=30)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        for table in BUSINESS_TABLES:
            if _count(db, table) is not None:
                db.execute(f'DELETE FROM "{table}"')
        db.commit()
        accounts_after = _count(db, "accounts")
    if accounts_after != plan["kept"]["accounts"]:
        raise RuntimeError("accounts_changed_unexpectedly")
    with closing(sqlite3.connect(runs_db, timeout=30)) as db:
        for table in RUN_STORE_TABLES:
            db.execute(f'DELETE FROM "{table}"')
        db.commit()
        db.execute("VACUUM")
    if office_db.is_file():
        with closing(sqlite3.connect(office_db, timeout=30)) as db:
            for table in OFFICE_TABLES:
                if _count(db, table) is not None:
                    db.execute(f'DELETE FROM "{table}"')
            db.commit()
    for name in FILE_DIRECTORIES:
        directory = files_root / name
        if directory.is_dir():
            for child in directory.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    with closing(sqlite3.connect(business_db, timeout=30)) as db:
        db.execute("VACUUM")
    plan["applied"] = True
    return plan


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-db", type=Path, required=True)
    parser.add_argument("--runs-db", type=Path, required=True)
    parser.add_argument("--office-db", type=Path, required=True)
    parser.add_argument("--files-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-wipe", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        reset(args.business_db, args.runs_db, args.office_db, args.files_root,
              apply=args.apply, confirm_wipe=args.confirm_wipe),
        ensure_ascii=False, indent=1,
    ))
