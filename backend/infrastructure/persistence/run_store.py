"""Small durable RunStore adapter for local and single-process deployments."""

import asyncio
import json
import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from backend.contracts.events.streaming import StreamEvent
from backend.contracts.harness.lifecycle import PersistedSession


class RunStoreError(RuntimeError):
    """The scheduler state could not be read or written safely."""


class JsonRunStore:
    """Atomically persist control-plane state in one JSON document.

    This adapter is intentionally a local first step. It is safe against a
    partially written file, but it is not a distributed multi-process store.
    The scheduler depends on the ``RunStore`` port, so a database adapter can
    replace it later without changing lifecycle contracts.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = asyncio.Lock()

    async def load(self, *, session_id: str) -> PersistedSession | None:
        async with self._lock:
            document = await asyncio.to_thread(self._read_document)
            raw = document.get(session_id)
            if raw is None:
                return None
            try:
                return PersistedSession.model_validate(raw)
            except ValueError as exc:
                raise RunStoreError("invalid persisted scheduler state") from exc

    async def list_sessions(self) -> list[str]:
        async with self._lock:
            return list(await asyncio.to_thread(self._read_document))

    async def save(self, state: PersistedSession) -> None:
        async with self._lock:
            document = await asyncio.to_thread(self._read_document)
            serialized = state.model_dump(mode="json")
            for saved, run in zip(
                serialized["snapshot"]["runs"], state.snapshot.runs, strict=True
            ):
                saved["execution_state"] = run.execution_state
            document[state.snapshot.session_id] = serialized
            await asyncio.to_thread(self._write_document, document)

    def _read_document(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RunStoreError("cannot read persisted scheduler state") from exc
        if not isinstance(document, dict):
            raise RunStoreError("persisted scheduler state must be an object")
        return document

    def _write_document(self, document: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"{self.path.name}.tmp")
            temporary.write_text(
                json.dumps(document, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            raise RunStoreError("cannot write persisted scheduler state") from exc


class SQLiteRunStore:
    """Single-service durable checkpoints and public events, behind the same port.

    The previous JSON store is a read-only migration source. Each session is
    imported once; existing files and user records are never deleted.
    """

    def __init__(self, path: str | Path, *, legacy_path: str | Path | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.legacy = JsonRunStore(legacy_path) if legacy_path else None
        self._event_db = None
        self._event_lock = threading.RLock()
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS run_sessions (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS run_events (run_id TEXT NOT NULL, sequence INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(run_id, sequence))")
            db.execute("CREATE TABLE IF NOT EXISTS run_visible (run_id TEXT PRIMARY KEY, text TEXT NOT NULL, sequence INTEGER NOT NULL)")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def owner(self):
        """Only one local service may execute work from this checkpoint store."""
        handle = self.path.with_suffix(self.path.suffix + ".lock").open("a+b")
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            self.close()
            handle.close()  # OS releases the lock after a crash as well.

    def close(self):
        with self._event_lock:
            if self._event_db is not None:
                self._event_db.close()
                self._event_db = None

    def __del__(self):
        self.close()

    async def load(self, *, session_id: str) -> PersistedSession | None:
        with self._connection() as db:
            row = db.execute("SELECT body FROM run_sessions WHERE id=?", (session_id,)).fetchone()
        if row:
            state = PersistedSession.model_validate_json(row[0])
            for run in state.snapshot.runs:
                text, sequence = self.visible(run.run_id)
                run.last_sequence = max(run.last_sequence, sequence)
                if text and run.status != "completed":
                    run.partial_output = text
            return state
        value = await self.legacy.load(session_id=session_id) if self.legacy else None
        if value:
            await self.save(value)
        return value

    async def save(self, state: PersistedSession) -> None:
        serialized = state.model_dump(mode="json")
        for saved, run in zip(serialized["snapshot"]["runs"], state.snapshot.runs, strict=True):
            saved["execution_state"] = run.execution_state
        with self._connection() as db:
            db.execute("INSERT INTO run_sessions(id,body) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body", (state.snapshot.session_id, json.dumps(serialized, ensure_ascii=False, separators=(",", ":"))))

    async def list_sessions(self) -> list[str]:
        with self._connection() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM run_sessions")]
        legacy = await self.legacy.list_sessions() if self.legacy else []
        return list(dict.fromkeys(ids + legacy))

    def append_event(self, event: StreamEvent) -> StreamEvent:
        with self._event_lock:
            if self._event_db is None:
                self._event_db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
                # Keep WAL open across chunks; closing the final connection on
                # every token forces checkpoints and disk syncs on Windows.
                self._event_db.execute("PRAGMA synchronous=NORMAL")
            return self._append_event(event)

    def _append_event(self, event):
        with self._event_db as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT text, sequence FROM run_visible WHERE run_id=?", (event.run_id,)).fetchone()
            text, sequence = row if row else ("", 0)
            sequence += 1
            event = event.model_copy(update={"sequence": sequence}, deep=True)
            if event.type == "delta":
                text += event.text or ""
            elif event.type in {"answer", "done"}:
                text = event.text or ""
            db.execute("INSERT INTO run_events VALUES (?,?,?)", (event.run_id, sequence, event.model_dump_json()))
            db.execute("INSERT INTO run_visible VALUES (?,?,?) ON CONFLICT(run_id) DO UPDATE SET text=excluded.text,sequence=excluded.sequence", (event.run_id, text, sequence))
        return event

    def stream_events_since(self, *, run_id: str, after_sequence: int = 0) -> list[StreamEvent]:
        with self._connection() as db:
            rows = db.execute("SELECT body FROM run_events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 1000", (run_id, after_sequence)).fetchall()
        return [StreamEvent.model_validate_json(row[0]) for row in rows]

    def visible(self, run_id: str) -> tuple[str, int]:
        with self._connection() as db:
            row = db.execute("SELECT text,sequence FROM run_visible WHERE run_id=?", (run_id,)).fetchone()
        return row if row else ("", 0)
