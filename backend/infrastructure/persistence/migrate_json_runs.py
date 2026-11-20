"""Explicit one-time migration from the retired JSON RunStore to SQLite.

The application never calls this module during startup. It is deliberately a
separate command so storage migration is visible, repeatable, and fail-fast.
"""

import argparse
import asyncio
import json
from pathlib import Path

from backend.contracts.harness.lifecycle import PersistedSession
from backend.infrastructure.persistence.run_store import SQLiteRunStore

STATUS_MAP = {
    "completed": "succeeded",
    "interrupted": "paused",
    "stopped": "cancelled",
}


def migrate_document(raw: dict) -> list[PersistedSession]:
    sessions: list[PersistedSession] = []
    for session_id, body in raw.items():
        if not isinstance(body, dict):
            raise ValueError(f"invalid_session:{session_id}")
        snapshot = body.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError(f"invalid_snapshot:{session_id}")
        for run in snapshot.get("runs", []):
            if not isinstance(run, dict):
                raise ValueError(f"invalid_run:{session_id}")
            status = run.get("status")
            if status in STATUS_MAP:
                run["status"] = STATUS_MAP[status]
        sessions.append(PersistedSession.model_validate(body))
    return sessions


async def migrate(source: Path, target: Path) -> int:
    if not source.is_file():
        raise FileNotFoundError(source)
    if target.exists():
        raise FileExistsError(f"target_exists:{target}")
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("json_store_root_must_be_object")
    sessions = migrate_document(raw)
    store = SQLiteRunStore(target)
    for session in sessions:
        await store.save(session)
    store.close()
    return len(sessions)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate a retired JSON RunStore to SQLite")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    count = asyncio.run(migrate(args.source, args.target))
    print(f"migrated_sessions={count}")


if __name__ == "__main__":
    main()
