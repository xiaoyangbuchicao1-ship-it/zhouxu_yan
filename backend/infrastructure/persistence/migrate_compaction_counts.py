"""Explicit offline migration of confirmed context-compaction counts.

Without --apply this opens both databases read-only. Applying requires the API
to be stopped and creates paired SQLite backups before any changes. Original
records, event bytes, output text and artifact metadata are never rewritten.
"""

import argparse
import hashlib
import json
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path

from backend.infrastructure.persistence.migrate_run_statuses import open_database
from backend.infrastructure.persistence.run_store import run_store_owner

FIELD = "context_compaction_count"


def confirmed_count(run: dict) -> int:
    """Count distinct, committed semantic summaries for this Run only."""
    summaries = set()
    for record in run.get("execution_state", {}).get("records", []):
        if record.get("source") != "memory" or record.get("run_id") != run["run_id"]:
            continue
        body = record.get("body", {})
        if not body.get("covered_record_ids"):
            continue
        summary = body.get("text")
        if not isinstance(summary, str) or not summary.strip():
            continue
        try:
            value = json.loads(summary)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and value.get("status") == "reference_only":
            continue
        summaries.add(record["record_id"])
    return len(summaries)


def prepare(runtime, workspace):
    sessions, projections, counts = [], [], {}
    changed_runs = 0
    for session_id, body in runtime.execute("SELECT id,body FROM run_sessions"):
        document = json.loads(body)
        changed = False
        for run in document["snapshot"]["runs"]:
            run_id = run["run_id"]
            if run_id in counts:
                raise ValueError("duplicate_run_identity")
            count = confirmed_count(run)
            counts[run_id] = count
            state = run.setdefault("execution_state", {})
            # Existing positive counters must have retained receipts. Never
            # silently lower a value when its evidence needs investigation.
            for location in (run, state):
                existing = location.get(FIELD, 0)
                if type(existing) is not int or existing < 0:
                    raise ValueError("invalid_compaction_count")
                if existing > count:
                    raise ValueError("compaction_count_without_retained_evidence")
            if run.get(FIELD) != count or state.get(FIELD) != count:
                run[FIELD] = state[FIELD] = count
                changed_runs += 1
                changed = True
        if changed:
            sessions.append((json.dumps(document, ensure_ascii=False), session_id))
    for run_id, raw in workspace.execute("SELECT run_id,progress_json FROM conversation_runs"):
        progress = json.loads(raw or "{}")
        if not isinstance(progress, dict):
            raise ValueError("invalid_run_progress")
        # Projection-only historical rows have no confirmed summary receipt.
        count = counts.get(run_id, 0)
        if progress.get(FIELD) != count:
            progress[FIELD] = count
            projections.append((json.dumps(progress, ensure_ascii=False), run_id))
    return sessions, projections, changed_runs, sum(counts.values())


def database_digest(database) -> str:
    digest = hashlib.sha256()
    for statement in database.iterdump():
        digest.update(statement.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def unchanged_digest(database, kind: str) -> str:
    """Cover all logical database data except the exact migrated fields."""
    digest = hashlib.sha256()
    tables = database.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    for (table,) in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        columns = [row[1] for row in database.execute(f"PRAGMA table_info({quoted})")]
        rows = []
        for values in database.execute(f"SELECT * FROM {quoted}"):
            row = dict(zip(columns, values, strict=True))
            if kind == "runtime" and table == "run_sessions":
                document = json.loads(row["body"])
                for run in document["snapshot"]["runs"]:
                    run.pop(FIELD, None)
                    state = run.get("execution_state", {})
                    state.pop(FIELD, None)
                    if not state:
                        run.pop("execution_state", None)
                row["body"] = document
            elif kind == "workspace" and table == "conversation_runs":
                progress = json.loads(row["progress_json"] or "{}")
                progress.pop(FIELD, None)
                row["progress_json"] = progress
            rows.append(json.dumps(row, sort_keys=True, ensure_ascii=False, default=lambda blob: {"bytes": blob.hex()}))
        digest.update(json.dumps([table, sorted(rows)], ensure_ascii=False).encode("utf-8"))
    return digest.hexdigest()


def migrate(run_store: Path, workspace_db: Path, *, backup_dir: Path | None = None) -> dict:
    with ExitStack() as stack:
        if backup_dir is not None:
            stack.enter_context(run_store_owner(run_store))
        runtime = stack.enter_context(closing(open_database(run_store, writable=backup_dir is not None)))
        workspace = stack.enter_context(closing(open_database(workspace_db, writable=backup_dir is not None)))
        for database in (runtime, workspace):
            if database.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("database_integrity_failed")
        sessions, projections, runs, confirmed = prepare(runtime, workspace)
        result = {"sessions": len(sessions), "runs": runs, "projections": len(projections),
                  "confirmed_compactions": confirmed, "events_changed": 0, "applied": False}
        if backup_dir is None or not (sessions or projections):
            return result
        before = (unchanged_digest(runtime, "runtime"), unchanged_digest(workspace, "workspace"))
        backup_dir.mkdir(parents=True, exist_ok=False)
        for source, name in ((runtime, "runs.sqlite3"), (workspace, "zhouxu.db")):
            with closing(sqlite3.connect(backup_dir / name)) as backup:
                source.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or database_digest(source) != database_digest(backup):
                    raise RuntimeError("backup_verification_failed")
        with runtime, workspace:
            runtime.execute("BEGIN IMMEDIATE")
            workspace.execute("BEGIN IMMEDIATE")
            runtime.executemany("UPDATE run_sessions SET body=? WHERE id=?", sessions)
            workspace.executemany("UPDATE conversation_runs SET progress_json=? WHERE run_id=?", projections)
            after = (unchanged_digest(runtime, "runtime"), unchanged_digest(workspace, "workspace"))
            if before != after:
                raise RuntimeError("migration_changed_unrelated_data")
            remaining = prepare(runtime, workspace)
            if remaining[0] or remaining[1]:
                raise RuntimeError("compaction_count_migration_incomplete")
        return {**result, "applied": True, "backup_dir": str(backup_dir)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-store", type=Path, required=True)
    parser.add_argument("--workspace-db", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    if args.apply != (args.backup_dir is not None):
        parser.error("--apply and --backup-dir are required together")
    print(json.dumps(migrate(args.run_store, args.workspace_db, backup_dir=args.backup_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
