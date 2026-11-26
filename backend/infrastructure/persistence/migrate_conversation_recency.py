"""Explicit data repair. Run with the API stopped, against the business database.

Every restart used to re-project all historical Runs and move each
conversation's ``updated_at`` to the restart time, so the sidebar order stopped
meaning "last used".  This resets ``updated_at`` for conversations that have
Runs to the time of their latest Run activity (never earlier than the
conversation's creation).  Only ``updated_at`` changes, and only when it is
later than that; the preflight (no ``--apply``) changes nothing.  A new backup
path is mandatory for apply.
"""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def _fingerprint(db: sqlite3.Connection) -> str:
    """Hash of everything except conversations.updated_at."""

    digest = hashlib.sha256()
    for name in ("conversation_runs", "conversation_materials", "projects"):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone():
            rows = db.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
            digest.update(json.dumps([name, rows], ensure_ascii=False, default=str).encode())
    columns = [row[1] for row in db.execute("PRAGMA table_info(conversations)") if row[1] != "updated_at"]
    rows = db.execute(f'SELECT {",".join(columns)} FROM conversations ORDER BY id').fetchall()
    digest.update(json.dumps(["conversations", rows], ensure_ascii=False, default=str).encode())
    return digest.hexdigest()


def plan(db: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """(conversation id, current updated_at, latest run activity) for each repair."""

    changes = []
    for conversation_id, created_at, updated_at in db.execute(
        "SELECT id, created_at, updated_at FROM conversations"
    ).fetchall():
        latest = db.execute(
            "SELECT MAX(COALESCE(completed_at, last_event_at, created_at)) "
            "FROM conversation_runs WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()[0]
        if latest is None:
            continue
        target = max(str(created_at), str(latest))
        if str(updated_at) > target:
            changes.append((conversation_id, str(updated_at), target))
    return changes


def migrate(path: Path, *, apply: bool = False, backup: Path | None = None) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("business_database_integrity_failed")
        changes = plan(db)
        before = _fingerprint(db)
        result = {
            "conversations": db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
            "to_repair": len(changes),
            "sample": [list(item) for item in changes[:5]],
            "applied": False,
        }
        if not apply or not changes:
            return result
        if backup is None or backup.exists():
            raise ValueError("a_new_backup_path_is_required")
        backup.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(backup)) as saved:
            db.backup(saved)
            if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("business_backup_integrity_failed")
        db.executemany(
            "UPDATE conversations SET updated_at=? WHERE id=?",
            [(target, conversation_id) for conversation_id, _, target in changes],
        )
        if _fingerprint(db) != before:
            db.rollback()
            raise RuntimeError("business_existing_data_changed")
        db.commit()
        result["applied"] = True
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.database.resolve(), apply=args.apply, backup=args.backup), ensure_ascii=False))
