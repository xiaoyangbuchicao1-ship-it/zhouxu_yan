"""What the system is made of, read live: registered tools and data stores.

Nothing here is a hand-written list.  Tools come from the Harness registry
that actually serves requests; stores and sizes come from the files on disk;
usage comes from the metrics recorded on real calls.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from backend.config.settings import BACKEND_ROOT
from backend.contracts.modules.loop import mode_tool_specs
from backend.infrastructure.telemetry.metrics import MetricsStore, source_of

SOURCE_LABELS = {
    "laws": "法规库",
    "cases": "案例库",
    "papers": "论文库",
    "web": "联网",
    "materials": "用户资料",
    "skills": "技能",
    "sandbox": "沙箱",
    "other": "其他",
}
SOURCE_ORDER = list(SOURCE_LABELS)

STORE_LABELS = {
    "zhouxu.db": ("业务库", "账户、会话、模型配置、审计"),
    "runs.sqlite3": ("运行库", "任务检查点与事件流"),
    "maritime-office.sqlite3": ("办公库", "用户上传的技能"),
    "maritime-corpus.sqlite3": ("本地语料索引", "法规与案例的本地兜底检索"),
    "maritime-corpus-citations.sqlite3": ("引用句柄", "回答里“查看原文”的来源缓存"),
    "telemetry.sqlite3": ("调用统计", "工具调用与检索的耗时和结果"),
}
# Row counts of the big corpus index would scan hundreds of MB; show size only.
_SKIP_ROW_COUNTS = {"maritime-corpus.sqlite3"}

_DIR_CACHE: dict[str, tuple[float, tuple[int, int]]] = {}
_DIR_RUNNING: set[str] = set()
_DIR_LOCK = threading.Lock()


def _scan_tree(path: Path) -> tuple[int, int]:
    size = files = 0
    stack = [str(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            size += entry.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return size, files


def _tree_stats(path: Path, *, ttl: float = 600.0) -> tuple[int, int] | None:
    """(bytes, files) under ``path`` without ever making the caller wait.

    The research tree is tens of thousands of files and a full scan can take a
    minute on a mounted disk, so it runs in the background.  Until the first
    scan finishes this returns None; afterwards a stale value is returned
    while a fresh one is computed.
    """

    key = str(path)
    with _DIR_LOCK:
        cached = _DIR_CACHE.get(key)
        fresh = cached is not None and time.time() - cached[0] < ttl
        start = not fresh and key not in _DIR_RUNNING
        if start:
            _DIR_RUNNING.add(key)

    if start:
        def work() -> None:
            try:
                result = _scan_tree(path) if path.is_dir() else (0, 0)
                with _DIR_LOCK:
                    _DIR_CACHE[key] = (time.time(), result)
            finally:
                with _DIR_LOCK:
                    _DIR_RUNNING.discard(key)

        threading.Thread(target=work, name="admin-disk-scan", daemon=True).start()
    return cached[1] if cached else None


def _first_sentence(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())
    for mark in ("。", "；", ". "):
        cut = text.find(mark)
        if 0 < cut < limit:
            return text[: cut + 1].strip()
    return text[:limit]


class AdminCatalogService:
    def __init__(self, settings, database_path: Path | None, runtime_dir: Path):
        self.settings = settings
        self.database_path = database_path
        self.runtime_dir = runtime_dir
        self.data_root = BACKEND_ROOT.parent / "data"

    @classmethod
    def from_settings(cls, settings) -> "AdminCatalogService":
        url = str(settings.database.url)
        database_path = None
        if url.startswith("sqlite:///"):
            database_path = Path(url[len("sqlite:///"):])
            if not database_path.is_absolute():
                database_path = (BACKEND_ROOT.parent / database_path).resolve()
        runtime = Path(settings.application.get("run_store_path", "backend/.runtime/runs.sqlite3"))
        if not runtime.is_absolute():
            runtime = BACKEND_ROOT.parent / runtime
        return cls(settings, database_path, runtime.parent)

    def disk_targets(self) -> list[tuple[str, Path]]:
        return [
            ("用户上传资料", self.data_root / "files" / "originals"),
            ("资料解析结果", self.data_root / "files" / "derived"),
            ("任务生成文件", self.data_root / "files" / "generated"),
            ("论文与研究资料", self.data_root / "research"),
            ("案例原始数据", self.data_root / "cases"),
        ]

    def warm(self) -> None:
        """Start the slow disk scans now so the page is quick when first opened."""

        for _, path in self.disk_targets():
            _tree_stats(path)

    # ------------------------------------------------------------ tools
    def tools(self, harness, metrics: MetricsStore | None, source_health: dict[str, dict], days: int = 7) -> dict:
        specs = list(getattr(harness, "tools", []))
        for extra in (getattr(harness, "material_tool", None), getattr(harness, "material_search_tool", None)):
            if extra is not None:
                specs.append(extra.spec)
        action_module = getattr(harness, "modules", {}).get("action") if hasattr(harness, "modules") else None
        chat = {tool.name for tool in mode_tool_specs(specs, "chat", action_module=action_module)}
        work = {tool.name for tool in mode_tool_specs(specs, "work", action_module=action_module)}
        # Material tools are scoped to the run, not to the mode list.
        for extra in (getattr(harness, "material_tool", None), getattr(harness, "material_search_tool", None)):
            if extra is not None:
                chat.add(extra.spec.name)
                work.add(extra.spec.name)
        usage = metrics.tool_summary(days=days) if metrics else {}
        rows = []
        for spec in specs:
            source = source_of(spec.name)
            health = source_health.get(source) or {"status": "ready", "reason": "无外部依赖"}
            stats = usage.get(spec.name)
            properties = (spec.parameters or {}).get("properties", {}) if isinstance(spec.parameters, dict) else {}
            required = set((spec.parameters or {}).get("required", [])) if isinstance(spec.parameters, dict) else set()
            rows.append(
                {
                    "name": spec.name,
                    "summary": _first_sentence(spec.description),
                    "kind": spec.kind,
                    # Reading a skill changes nothing even though its contract is mode-scoped.
                    "read_only": bool(spec.read_only) or spec.kind == "skill_activation",
                    "chat": spec.name in chat,
                    "work": spec.name in work,
                    "source": source,
                    "source_label": SOURCE_LABELS.get(source, source),
                    "status": health.get("status", "ready"),
                    "status_reason": health.get("reason"),
                    "parameters": [{"name": key, "required": key in required} for key in properties],
                    "calls": int(stats["calls"]) if stats else 0,
                    "errors": int(stats["errors"] or 0) if stats else 0,
                    "empty": int(stats["empty"] or 0) if stats else 0,
                    "avg_ms": stats["avg_ms"] if stats else None,
                    "last_call_at": stats["last_ts"] if stats else None,
                    "last_error_code": stats["last_error_code"] if stats else None,
                }
            )
        rows.sort(key=lambda row: (SOURCE_ORDER.index(row["source"]) if row["source"] in SOURCE_ORDER else 99, row["name"]))
        calls = sum(row["calls"] for row in rows)
        errors = sum(row["errors"] for row in rows)
        return {
            "window_days": days,
            "recording": metrics is not None,
            "summary": {
                "total": len(rows),
                "chat": sum(1 for row in rows if row["chat"]),
                "work": sum(1 for row in rows if row["work"]),
                "calls": calls,
                "errors": errors,
            },
            "tools": rows,
        }

    # ------------------------------------------------------------ data
    def data(self, resource_health: dict, metrics: MetricsStore | None) -> dict:
        usage = metrics.source_summary(hours=24) if metrics else {}
        corpus = []
        for key in ("laws", "cases", "papers"):
            health = (resource_health.get("sources") or {}).get(key, {})
            stats = usage.get(key)
            corpus.append(
                {
                    "key": key,
                    "label": SOURCE_LABELS[key],
                    "status": health.get("status", "unavailable"),
                    "count": health.get("count"),
                    "reason": health.get("reason"),
                    "searches_24h": int(stats["calls"]) if stats else 0,
                    "empty_24h": int(stats["empty"] or 0) if stats else 0,
                    "errors_24h": int(stats["errors"] or 0) if stats else 0,
                    "avg_ms_24h": stats["avg_ms"] if stats else None,
                }
            )
        stores = []
        paths = []
        if self.database_path is not None:
            paths.append(self.database_path)
        if self.runtime_dir.is_dir():
            paths.extend(sorted(self.runtime_dir.glob("*.sqlite3")))
        seen = set()
        for path in paths:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            label, purpose = STORE_LABELS.get(path.name, (path.name, ""))
            wal = path.with_name(path.name + "-wal")
            tables = []
            if path.name not in _SKIP_ROW_COUNTS:
                try:
                    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)) as db:
                        for (name,) in db.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                        ).fetchall():
                            tables.append({"name": name, "rows": db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]})
                except sqlite3.Error:
                    tables = []
            stores.append(
                {
                    "file": path.name,
                    "label": label,
                    "purpose": purpose,
                    "size_bytes": path.stat().st_size + (wal.stat().st_size if wal.is_file() else 0),
                    "modified_at": path.stat().st_mtime,
                    "tables": tables,
                }
            )
        backups = []
        backup_dir = self.runtime_dir / "backups"
        if backup_dir.is_dir():
            for item in sorted(backup_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
                if item.is_file():
                    backups.append({"name": item.name, "size_bytes": item.stat().st_size, "modified_at": item.stat().st_mtime})
        disk = []
        for label, path in self.disk_targets():
            stats = _tree_stats(path)
            disk.append(
                {
                    "label": label,
                    "size_bytes": stats[0] if stats else None,
                    "files": stats[1] if stats else None,
                }
            )
        return {
            "recording": metrics is not None,
            "corpus": corpus,
            "stores": stores,
            "backups": backups,
            "disk": disk,
        }
