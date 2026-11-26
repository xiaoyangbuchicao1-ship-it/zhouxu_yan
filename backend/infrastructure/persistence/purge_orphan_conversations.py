"""Explicit cleanup of what already-deleted conversations left behind.

Deleting a conversation used to remove only its business rows.  This finds the
leftovers: Run checkpoints, events and visible text in the run store, and the
generated-file / skill-copy directories on disk, for conversation ids that no
longer exist in the business database.  Run with the API stopped.  Without
``--apply`` nothing is changed.  The owner decided that deletions need no
backup, so none is taken.
"""

import argparse
import json
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

_CONVERSATION_ID = re.compile(r"[0-9a-f]{32}")


def scan(business_db: Path, runs_db: Path, workspace_root: Path) -> dict:
    with closing(sqlite3.connect(f"{business_db.as_uri()}?mode=ro", uri=True)) as db:
        live = {row[0] for row in db.execute("SELECT id FROM conversations")}
    sessions: dict[str, list[str]] = {}
    with closing(sqlite3.connect(f"{runs_db.as_uri()}?mode=ro", uri=True)) as db:
        for session_id, body in db.execute("SELECT id, body FROM run_sessions"):
            if _CONVERSATION_ID.fullmatch(session_id) and session_id not in live:
                runs = json.loads(body).get("snapshot", {}).get("runs", [])
                sessions[session_id] = [item["run_id"] for item in runs if isinstance(item, dict) and item.get("run_id")]
    directories: list[Path] = []
    for base in (workspace_root, workspace_root / ".skill-packages"):
        if not base.is_dir():
            continue
        for account in base.iterdir():
            if not account.is_dir() or account.name.startswith("."):
                continue
            for conversation in account.iterdir():
                if conversation.is_dir() and _CONVERSATION_ID.fullmatch(conversation.name) and conversation.name not in live:
                    directories.append(conversation)
    return {"sessions": sessions, "directories": directories}


def purge(business_db: Path, runs_db: Path, workspace_root: Path, *, apply: bool = False) -> dict:
    found = scan(business_db, runs_db, workspace_root)
    result = {
        "orphan_sessions": len(found["sessions"]),
        "orphan_runs": sum(len(items) for items in found["sessions"].values()),
        "orphan_directories": len(found["directories"]),
        "applied": False,
    }
    if not apply:
        return result
    root = workspace_root.resolve()
    with closing(sqlite3.connect(runs_db, timeout=30)) as db:
        for session_id, run_ids in found["sessions"].items():
            db.executemany("DELETE FROM run_events WHERE run_id=?", [(run_id,) for run_id in run_ids])
            db.executemany("DELETE FROM run_visible WHERE run_id=?", [(run_id,) for run_id in run_ids])
            db.execute("DELETE FROM run_sessions WHERE id=?", (session_id,))
        db.commit()
    for directory in found["directories"]:
        resolved = directory.resolve()
        if root in resolved.parents and resolved.is_dir():
            shutil.rmtree(resolved)
    result["applied"] = True
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-db", type=Path, required=True)
    parser.add_argument("--runs-db", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        purge(args.business_db.resolve(), args.runs_db.resolve(), args.workspace_root.resolve(), apply=args.apply),
        ensure_ascii=False,
    ))
