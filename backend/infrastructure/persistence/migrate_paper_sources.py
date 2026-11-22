"""Explicit offline paper-source cutover; historical prose and tool receipts stay intact.

Preview is read-only. Apply requires the API's owner lock and verified paired
SQLite backups. Only display references and one protected catalog correction
for resumable runs change; active retrieval never retains the retired cards.
"""

import argparse
import hashlib
import json
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path

from backend.infrastructure.persistence.migrate_run_statuses import open_database
from backend.infrastructure.persistence.migrate_compaction_counts import database_digest
from backend.infrastructure.persistence.run_store import run_store_owner
from backend.infrastructure.sources import _paper_public_text


def load_rows(path):
    with path.open(encoding="utf-8-sig") as stream:
        return [json.loads(line) for line in stream if line.strip()]


class Cutover:
    def __init__(self, retired, papers):
        self.retired = {row["card_id"]: row["replacement_document_id"] for row in load_rows(retired)}
        self.papers = {row["document_id"]: row for row in load_rows(papers)}
        if any(document not in self.papers for document in self.retired.values()):
            raise ValueError("replacement_original_missing")

    def refs(self, refs):
        result = []
        for raw in refs:
            ref = dict(raw)
            old = ref["source_id"].removeprefix("paper-")
            if old in self.retired:
                document = self.retired[old]
                ref.update(source_id="paper-document-" + document,
                           title=_paper_public_text(self.papers[document]["title"]), locator=None, section=None)
            if ref["source_id"] not in {row["source_id"] for row in result}:
                result.append(ref)
        return result

    def old_ids(self, value):
        if isinstance(value, dict):
            return set().union(*(self.old_ids(child) for child in value.values()))
        if isinstance(value, list):
            return set().union(*(self.old_ids(child) for child in value))
        return {value} if isinstance(value, str) and value in self.retired else set()

    def record(self, run, old_ids):
        documents = sorted({self.retired[card] for card in old_ids})
        return {
            "record_id": "paper-catalog-cutover:" + run["run_id"],
            "session_id": run["session_id"], "run_id": run["run_id"],
            "task_id": run.get("task_id"), "kind": "correction", "source": "corpus_release",
            "summary": "论文资料已更新，后续读取使用新版检索结果。",
            "body": {"instruction": "这些历史卡片已被新版论文资料替换，回执保留为原始记录。后续需要再次读取论据时，使用 information_card_search 按论文标题重新检索；不要再调用旧卡编号。",
                     "retired_card_ids": sorted(old_ids),
                     "papers": [{"title": _paper_public_text(self.papers[document]["title"]), "source_id": "paper-document-" + document} for document in documents]},
            "status": "available", "retention": "run", "created_at": "2026-11-21T00:00:00Z",
        }

    def run(self, run):
        state = run.get("execution_state", {})
        changed = False
        if "source_refs" in state:
            refs = self.refs(state["source_refs"])
            changed = refs != state["source_refs"]
            state["source_refs"] = refs
        marker = "paper-catalog-cutover:" + run["run_id"]
        if run["status"] in {"paused", "failed", "waiting_user"} and not any(record.get("record_id") == marker for record in state.get("records", [])):
            old_ids = self.old_ids(state)
            if old_ids:
                state.setdefault("records", []).append(self.record(run, old_ids))
                changed = True
        return changed


def prepare(runtime, workspace, cutover):
    sessions, projections, events, runs = [], [], [], 0
    for session_id, raw in runtime.execute("SELECT id,body FROM run_sessions"):
        document = json.loads(raw)
        changed = sum(cutover.run(run) for run in document["snapshot"]["runs"])
        runs += changed
        if changed:
            sessions.append((json.dumps(document, ensure_ascii=False), session_id))
    for run_id, raw in workspace.execute("SELECT run_id,progress_json FROM conversation_runs"):
        progress = json.loads(raw or "{}")
        if "source_refs" in progress:
            refs = cutover.refs(progress["source_refs"])
            if refs != progress["source_refs"]:
                progress["source_refs"] = refs
                projections.append((json.dumps(progress, ensure_ascii=False), run_id))
    for run_id, sequence, raw in runtime.execute("SELECT run_id,sequence,body FROM run_events"):
        event = json.loads(raw)
        # Public lifecycle snapshots contain display references, not tool receipts.
        run = event.get("run")
        if isinstance(run, dict) and "sources" in run:
            refs = cutover.refs(run["sources"])
            if refs != run["sources"]:
                run["sources"] = refs
                events.append((json.dumps(event, ensure_ascii=False), run_id, sequence))
    return sessions, projections, events, runs


def invariant_digest(database, kind, cutover):
    digest = hashlib.sha256()
    for (table,) in database.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted = '"' + table.replace('"', '""') + '"'
        columns = [row[1] for row in database.execute(f"PRAGMA table_info({quoted})")]
        rows = []
        for values in database.execute(f"SELECT * FROM {quoted}"):
            row = dict(zip(columns, values, strict=True))
            if kind == "runtime" and table == "run_sessions":
                body = json.loads(row["body"])
                for run in body["snapshot"]["runs"]:
                    state = run.get("execution_state", {})
                    if "source_refs" in state:
                        state["source_refs"] = cutover.refs(state["source_refs"])
                    if "records" in state:
                        state["records"] = [record for record in state["records"] if not (record.get("record_id") == "paper-catalog-cutover:" + run["run_id"] and record.get("source") == "corpus_release")]
                row["body"] = body
            elif kind == "workspace" and table == "conversation_runs":
                body = json.loads(row["progress_json"] or "{}")
                if "source_refs" in body:
                    body["source_refs"] = cutover.refs(body["source_refs"])
                row["progress_json"] = body
            elif kind == "runtime" and table == "run_events":
                body = json.loads(row["body"])
                if isinstance(body.get("run"), dict) and "sources" in body["run"]:
                    body["run"]["sources"] = cutover.refs(body["run"]["sources"])
                row["body"] = body
            rows.append(json.dumps(row, sort_keys=True, ensure_ascii=False, default=lambda blob: {"bytes": blob.hex()}))
        digest.update(json.dumps([table, sorted(rows)], ensure_ascii=False).encode())
    return digest.hexdigest()


def migrate(run_store, workspace_db, retired, papers, *, backup_dir=None):
    cutover = Cutover(retired, papers)
    with ExitStack() as stack:
        if backup_dir is not None:
            stack.enter_context(run_store_owner(run_store))
        runtime = stack.enter_context(closing(open_database(run_store, writable=backup_dir is not None)))
        workspace = stack.enter_context(closing(open_database(workspace_db, writable=backup_dir is not None)))
        for db in (runtime, workspace):
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("database_integrity_failed")
        sessions, projections, events, runs = prepare(runtime, workspace, cutover)
        result = {"sessions": len(sessions), "runs": runs, "projections": len(projections), "events": len(events), "applied": False}
        if backup_dir is None or not (sessions or projections or events):
            return result
        before = invariant_digest(runtime, "runtime", cutover), invariant_digest(workspace, "workspace", cutover)
        backup_dir.mkdir(parents=True, exist_ok=False)
        for source, filename in ((runtime, "runs.sqlite3"), (workspace, "zhouxu.db")):
            with closing(sqlite3.connect(backup_dir / filename)) as backup:
                source.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or database_digest(source) != database_digest(backup):
                    raise RuntimeError("backup_verification_failed")
        with runtime, workspace:
            runtime.execute("BEGIN IMMEDIATE")
            workspace.execute("BEGIN IMMEDIATE")
            runtime.executemany("UPDATE run_sessions SET body=? WHERE id=?", sessions)
            runtime.executemany("UPDATE run_events SET body=? WHERE run_id=? AND sequence=?", events)
            workspace.executemany("UPDATE conversation_runs SET progress_json=? WHERE run_id=?", projections)
            if before != (invariant_digest(runtime, "runtime", cutover), invariant_digest(workspace, "workspace", cutover)):
                raise RuntimeError("migration_changed_unrelated_data")
            if any(prepare(runtime, workspace, cutover)[:3]):
                raise RuntimeError("paper_source_migration_incomplete")
        return {**result, "applied": True, "backup_dir": str(backup_dir)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-store", type=Path, required=True)
    parser.add_argument("--workspace-db", type=Path, required=True)
    parser.add_argument("--retired", type=Path, required=True)
    parser.add_argument("--papers", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    if args.apply != (args.backup_dir is not None):
        parser.error("--apply and --backup-dir are required together")
    print(json.dumps(migrate(args.run_store, args.workspace_db, args.retired, args.papers, backup_dir=args.backup_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
