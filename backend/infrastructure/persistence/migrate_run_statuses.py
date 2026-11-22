"""Offline, explicit migration of persisted Run lifecycle status values.

Stop the API before applying. The default command only validates and reports;
--apply requires SQLite backups. Task-step statuses and user content are never
rewritten. This module is deliberately not imported by the application.
"""

import argparse
import json
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path

from pydantic import TypeAdapter

from backend.contracts.events.streaming import StreamEvent
from backend.contracts.harness.lifecycle import RunStatus
from backend.infrastructure.persistence.migrate_json_runs import (
    STATUS_MAP,
    migrate_document,
)
from backend.infrastructure.persistence.run_store import run_store_owner


def open_database(path: Path, *, writable: bool = False) -> sqlite3.Connection:
    mode = "rw" if writable else "ro"
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode={mode}", uri=True)


def prepare_runtime(db: sqlite3.Connection) -> dict[str, list[tuple]]:
    sessions, events = [], []
    for session_id, body in db.execute("SELECT id, body FROM run_sessions"):
        raw = json.loads(body)
        migrated = json.loads(body)
        migrate_document({session_id: migrated})
        if raw != migrated:
            sessions.append((json.dumps(migrated, ensure_ascii=False), session_id))
    for run_id, sequence, body in db.execute("SELECT run_id, sequence, body FROM run_events"):
        raw = json.loads(body)
        run = raw.get("run")
        if run is not None and run["status"] in STATUS_MAP:
            run["status"] = STATUS_MAP[run["status"]]
            events.append((json.dumps(raw, ensure_ascii=False), run_id, sequence))
        StreamEvent.model_validate(raw)
    return {"sessions": sessions, "events": events}


def prepare_workspace(db: sqlite3.Connection) -> list[tuple[str, str]]:
    validate = TypeAdapter(RunStatus)
    updates = []
    for (status,) in db.execute("SELECT DISTINCT status FROM conversation_runs"):
        canonical = STATUS_MAP.get(status, status)
        validate.validate_python(canonical)
        if canonical != status:
            updates.append((canonical, status))
    return updates


def migrate(run_store: Path, workspace_db: Path, *, backup_dir: Path | None = None) -> dict:
    with ExitStack() as stack:
        runtime = stack.enter_context(closing(open_database(run_store, writable=backup_dir is not None)))
        workspace = stack.enter_context(closing(open_database(workspace_db, writable=backup_dir is not None)))
        if backup_dir is not None:
            # The same owner lock as the server prevents migration under a
            # running scheduler. Both DB transactions roll back on validation
            # or write errors; offline backups also cover interruption between
            # the commits of these independent databases.
            stack.enter_context(run_store_owner(run_store))
            stack.enter_context(runtime)
            stack.enter_context(workspace)
            runtime.execute("BEGIN IMMEDIATE")
            workspace.execute("BEGIN IMMEDIATE")
        changes = prepare_runtime(runtime)
        workspace_updates = prepare_workspace(workspace)
        workspace_count = sum(
            workspace.execute("SELECT COUNT(*) FROM conversation_runs WHERE status=?", (old,)).fetchone()[0]
            for _, old in workspace_updates
        )
        counts = {
            "runtime_sessions": len(changes["sessions"]),
            "runtime_events": len(changes["events"]),
            "workspace_runs": workspace_count,
            "applied": backup_dir is not None,
        }
        if backup_dir is None or not any((changes["sessions"], changes["events"], workspace_updates)):
            return counts

        backup_dir.mkdir(parents=True, exist_ok=False)
        for source, name in ((run_store, "runs.sqlite3"), (workspace_db, "zhouxu.db")):
            with closing(open_database(source)) as original, closing(sqlite3.connect(backup_dir / name)) as backup:
                original.backup(backup)
        runtime.executemany("UPDATE run_sessions SET body=? WHERE id=?", changes["sessions"])
        runtime.executemany("UPDATE run_events SET body=? WHERE run_id=? AND sequence=?", changes["events"])
        workspace.executemany("UPDATE conversation_runs SET status=? WHERE status=?", workspace_updates)
        if any(prepare_runtime(runtime).values()) or prepare_workspace(workspace):
            raise RuntimeError("run_status_migration_incomplete")
        counts["backup_dir"] = str(backup_dir)
        return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-store", type=Path, required=True)
    parser.add_argument("--workspace-db", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    if args.apply != (args.backup_dir is not None):
        parser.error("--apply and --backup-dir must be provided together")
    print(json.dumps(migrate(args.run_store, args.workspace_db, backup_dir=args.backup_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
