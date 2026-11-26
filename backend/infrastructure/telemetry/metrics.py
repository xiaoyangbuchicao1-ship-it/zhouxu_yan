"""Real usage numbers for the administrator: every tool call, every search.

One small SQLite file next to the other runtime stores.  It holds only
metadata (which tool, how long, how it ended, which Run) and never prompts,
arguments or results.  Nothing is recorded until the application configures
a store, so tests and one-off scripts never write one by accident.
"""

import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path

# Which data source or capability a tool depends on.
_SOURCE_BY_TOOL = {
    "dynamic_rag_search": "laws",
    "search_maritime_corpus": "laws",
    "search_cases": "cases",
    "read_case": "cases",
    "information_card_search": "papers",
    "information_card_read": "papers",
    "capability_card_select": "papers",
    "capability_card_read": "papers",
    "web_search": "web",
    "web_read": "web",
    "read_material": "materials",
    "search_material": "materials",
    "read_skill": "skills",
    "read": "sandbox",
    "write": "sandbox",
    "edit": "sandbox",
    "bash": "sandbox",
    "paper_compile": "sandbox",
}


def source_of(tool: str) -> str:
    return _SOURCE_BY_TOOL.get(tool, "other")


class MetricsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS tool_calls ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, tool TEXT NOT NULL, "
                "source TEXT NOT NULL, outcome TEXT NOT NULL, duration_ms INTEGER NOT NULL, "
                "mode TEXT, account_id TEXT, conversation_id TEXT, run_id TEXT, error_code TEXT)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS tool_calls_ts ON tool_calls(ts)")
            db.execute("CREATE INDEX IF NOT EXISTS tool_calls_tool ON tool_calls(tool, ts)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record_tool(
        self,
        *,
        tool: str,
        outcome: str,
        duration_ms: int,
        source: str | None = None,
        mode: str | None = None,
        account_id: str | None = None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        try:
            with self._lock, self._connect() as db:
                db.execute(
                    "INSERT INTO tool_calls(ts,tool,source,outcome,duration_ms,mode,account_id,conversation_id,run_id,error_code) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (time.time(), tool, source or source_of(tool), outcome, max(0, int(duration_ms)), mode,
                     account_id, conversation_id, run_id, error_code),
                )
        except sqlite3.Error:
            # Statistics must never break a user's task.
            return

    def tool_summary(self, *, days: int = 7) -> dict[str, dict]:
        since = time.time() - days * 86400
        with closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT tool, COUNT(*) AS calls, "
                "SUM(outcome='error') AS errors, SUM(outcome='empty') AS empty, "
                "CAST(AVG(duration_ms) AS INTEGER) AS avg_ms, MAX(ts) AS last_ts "
                "FROM tool_calls WHERE ts>=? GROUP BY tool",
                (since,),
            ).fetchall()
            summary = {row["tool"]: dict(row) for row in rows}
            for tool, item in summary.items():
                last_error = db.execute(
                    "SELECT error_code, ts FROM tool_calls WHERE tool=? AND outcome='error' ORDER BY ts DESC LIMIT 1",
                    (tool,),
                ).fetchone()
                item["last_error_code"] = last_error["error_code"] if last_error else None
                item["last_error_ts"] = last_error["ts"] if last_error else None
        return summary

    def source_summary(self, *, hours: int = 24) -> dict[str, dict]:
        since = time.time() - hours * 3600
        with closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT source, COUNT(*) AS calls, SUM(outcome='empty') AS empty, SUM(outcome='error') AS errors, "
                "CAST(AVG(duration_ms) AS INTEGER) AS avg_ms FROM tool_calls WHERE ts>=? GROUP BY source",
                (since,),
            ).fetchall()
        return {row["source"]: dict(row) for row in rows}

    def recent_calls(self, tool: str, *, limit: int = 20) -> list[dict]:
        with closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT ts, outcome, duration_ms, mode, account_id, conversation_id, run_id, error_code "
                "FROM tool_calls WHERE tool=? ORDER BY ts DESC LIMIT ?",
                (tool, limit),
            ).fetchall()
        return [dict(row) for row in rows]


_store: MetricsStore | None = None


def configure(path: str | Path | None) -> MetricsStore | None:
    """Start (or stop, with None) recording.  Called once by the application."""

    global _store
    _store = MetricsStore(path) if path else None
    return _store


def get_store() -> MetricsStore | None:
    return _store
