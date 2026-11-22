"""Explicit offline checkpoint v2 migration. Default is a read-only preflight.

For old runs, today's workspace is a migration cutover, never a reconstructed
run-start snapshot. Existing artifact receipts retain ownership independently.
"""

import argparse
import hashlib
import json
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path

from backend.config.settings import load_settings
from backend.contracts.harness.execution import ExecutionState
from backend.infrastructure.execution.workspace import WorkspaceExecutionProvider
from backend.infrastructure.persistence.migrate_run_statuses import open_database
from backend.infrastructure.persistence.run_store import run_store_owner


def migrate_state(run, provider):
    if run.get("graph"):
        return False
    state = run.get("execution_state", {})
    if state.get("schema_version") == 2:
        ExecutionState.model_validate(state)
        return False
    if state.get("schema_version", 1) != 1:
        raise ValueError("unsupported_execution_checkpoint_version")
    state = dict(state)
    if state.get("workspace_baseline") is None:
        state["workspace_baseline"] = provider.snapshot_baseline(
            run["run_id"], account_id=run.get("account_id"),
            conversation_id=run.get("conversation_id") or run["session_id"])
        state["workspace_baseline_origin"] = "migration_cutover"
    else:
        state["workspace_baseline_origin"] = "run_start"
    state["schema_version"] = 2
    run["execution_state"] = ExecutionState.model_validate(state).model_dump(mode="json")
    return True


def migrate(run_store, workspace_db, *, backup_dir=None, settings=None):
    provider = WorkspaceExecutionProvider(settings or load_settings())
    with ExitStack() as stack:
        if backup_dir is not None:
            stack.enter_context(run_store_owner(run_store))
        runtime = stack.enter_context(closing(open_database(run_store, writable=backup_dir is not None)))
        workspace = stack.enter_context(closing(open_database(workspace_db, writable=backup_dir is not None)))
        for db in (runtime, workspace):
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("database_integrity_failed")
        sessions, projections, events = [], [], []
        count = 0
        file_checks = []
        for session_id, body in runtime.execute("SELECT id,body FROM run_sessions"):
            raw = json.loads(body)
            changed = False
            for run in raw["snapshot"]["runs"]:
                if not migrate_state(run, provider):
                    continue
                count += 1
                changed = True
                state = run["execution_state"]
                root = provider.workspace_root_for(run_id=run["run_id"], account_id=run.get("account_id"),
                    conversation_id=run.get("conversation_id") or run["session_id"])
                for artifact in state["artifacts"]:
                    path = (root / artifact["relative_path"]).resolve()
                    if not path.is_relative_to(root.resolve()):
                        raise ValueError("artifact_outside_workspace")
                    status = "missing"
                    if path.is_file():
                        digest = hashlib.sha256(path.read_bytes()).hexdigest()
                        status = "verified" if digest == artifact["sha256"] else "changed"
                    file_checks.append({"run_id": run["run_id"], "artifact_id": artifact["artifact_id"], "status": status})
                row = workspace.execute("SELECT progress_json FROM conversation_runs WHERE run_id=?", (run["run_id"],)).fetchone()
                if row:
                    progress = json.loads(row[0] or "{}")
                    progress["execution_schema_version"] = 2
                    progress["workspace_baseline_origin"] = state["workspace_baseline_origin"]
                    projections.append((json.dumps(progress, ensure_ascii=False), run["run_id"]))
            if changed:
                sessions.append((json.dumps(raw, ensure_ascii=False), session_id))
        for run_id, sequence, body in runtime.execute("SELECT run_id,sequence,body FROM run_events"):
            raw = json.loads(body)
            if isinstance(raw.get("run"), dict) and raw["run"].get("execution_state"):
                if migrate_state(raw["run"], provider):
                    events.append((json.dumps(raw, ensure_ascii=False), run_id, sequence))
        result = {"runs": count, "sessions": len(sessions), "events": len(events),
                  "projections": len(projections), "artifact_checks": file_checks,
                  "applied": False}
        if backup_dir is None or not (sessions or events):
            return result
        backup_dir.mkdir(parents=True, exist_ok=False)
        for source, name in ((runtime, "runs.sqlite3"), (workspace, "zhouxu.db")):
            with closing(sqlite3.connect(backup_dir / name)) as backup:
                source.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("backup_integrity_failed")
        with runtime, workspace:
            runtime.executemany("UPDATE run_sessions SET body=? WHERE id=?", sessions)
            runtime.executemany("UPDATE run_events SET body=? WHERE run_id=? AND sequence=?", events)
            workspace.executemany("UPDATE conversation_runs SET progress_json=? WHERE run_id=?", projections)
        result.update(applied=True, backup_dir=str(backup_dir))
        return result


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
