"""Safe, bounded storage maintenance and hot SQLite backup service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select

from backend.application.admin import AdminService
from backend.application.admin_catalog import _DIR_CACHE, _DIR_LOCK
from backend.config.settings import BACKEND_ROOT, Settings
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    ConversationModel,
    ConversationRunModel,
    EmailVerificationCodeModel,
    MaterialModel,
    SessionModel,
)
from backend.infrastructure.execution.workspace import _safe_part

_BACKUP_NAME_RE = re.compile(r"^zhouxu-backup-[A-Za-z0-9_-]+\.zip$")
_WARN_FREE_BYTES = 20 * 1024 * 1024 * 1024
_CRITICAL_FREE_BYTES = 5 * 1024 * 1024 * 1024
_ACTIVE_RUN_STATUSES = ("queued", "running", "pausing", "paused", "waiting_user")
_BACKUP_DB_NAMES = (
    "runs.sqlite3",
    "maritime-office.sqlite3",
    "telemetry.sqlite3",
    "maritime-corpus-citations.sqlite3",
)


def _dir_size(path: Path) -> tuple[int, int]:
    total = files = 0
    if not path.is_dir():
        return 0, 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
                files += 1
        except OSError:
            continue
    return total, files


def _resolve_root(configured: str) -> Path:
    path = Path(configured)
    return path.resolve() if path.is_absolute() else (BACKEND_ROOT.parent / path).resolve()


class RuntimeMaintenanceService:
    """Keeps scratch artifacts bounded and creates verified hot SQLite backups.

    Safety invariant: NEVER deletes active accounts, conversations, chat messages,
    run checkpoints, active uploaded materials, or deliverable PDF/DOCX artifacts.
    """

    def __init__(
        self,
        *,
        database: Database,
        settings: Settings,
        database_path: Path | None,
        runtime_dir: Path,
    ):
        self.database = database
        self.settings = settings
        self.database_path = database_path
        self.runtime_dir = runtime_dir
        self.data_root = BACKEND_ROOT.parent / "data"
        self.backups_dir = runtime_dir / "backups"
        self._lock = threading.Lock()
        self._last_cleanup_at: float | None = None
        self._last_cleanup_bytes: int = 0

    def host_storage(self) -> dict:
        target = self.data_root if self.data_root.exists() else self.runtime_dir
        usage = shutil.disk_usage(target)
        status = (
            "critical"
            if usage.free < _CRITICAL_FREE_BYTES
            else "warn"
            if usage.free < _WARN_FREE_BYTES
            else "ok"
        )
        return {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "status": status,
        }

    def _safe_cleanup_targets(self) -> tuple[list[tuple[str, Path, int, int]], dict]:
        """Collect only strictly safe temporary/orphaned directories and expired auth rows."""
        now_ts = time.time()
        min_age_seconds = 3600  # Avoid touching files from an ongoing upload/request.
        now_dt = datetime.now(timezone.utc)

        with self.database.session() as session:
            known_conv_safe = {
                _safe_part(cid)
                for cid in session.scalars(select(ConversationModel.id)).all()
            }
            active_conv_safe = {
                _safe_part(cid)
                for cid in session.scalars(
                    select(ConversationModel.id)
                    .join(ConversationRunModel, ConversationRunModel.conversation_id == ConversationModel.id)
                    .where(ConversationRunModel.status.in_(_ACTIVE_RUN_STATUSES))
                ).all()
            }
            known_materials = {
                mid for mid in session.scalars(select(MaterialModel.id)).all()
            }
            expired_sessions = session.query(SessionModel).filter(
                (SessionModel.expires_at < now_dt - timedelta(days=7))
                | (
                    SessionModel.revoked_at.is_not(None)
                    & (SessionModel.revoked_at < now_dt - timedelta(days=30))
                )
            ).count()
            expired_codes = session.query(EmailVerificationCodeModel).filter(
                EmailVerificationCodeModel.expires_at < now_dt - timedelta(days=1)
            ).count()

        targets: list[tuple[str, Path, int, int]] = []
        ws_root = _resolve_root(self.settings.execution.workspace_root)
        if ws_root.is_dir():
            for acct_dir in ws_root.iterdir():
                if not acct_dir.is_dir():
                    continue
                if acct_dir.name == ".skill-packages":
                    for sub_acct in acct_dir.iterdir():
                        if not sub_acct.is_dir():
                            continue
                        for conv_dir in sub_acct.iterdir():
                            if not conv_dir.is_dir():
                                continue
                            if conv_dir.name not in known_conv_safe and conv_dir.name not in active_conv_safe:
                                try:
                                    if now_ts - conv_dir.stat().st_mtime > min_age_seconds:
                                        b, f = _dir_size(conv_dir)
                                        targets.append(("orphan_skill_cache", conv_dir, b, f))
                                except OSError:
                                    continue
                    continue
                for conv_dir in acct_dir.iterdir():
                    if not conv_dir.is_dir():
                        continue
                    is_active = conv_dir.name in active_conv_safe
                    if not is_active and conv_dir.name not in known_conv_safe:
                        try:
                            if now_ts - conv_dir.stat().st_mtime > min_age_seconds:
                                b, f = _dir_size(conv_dir)
                                targets.append(("orphan_workspace", conv_dir, b, f))
                        except OSError:
                            continue
                        continue
                    if not is_active:
                        render_dir = conv_dir / ".zhouxu-render"
                        if render_dir.is_dir():
                            try:
                                if now_ts - render_dir.stat().st_mtime > min_age_seconds:
                                    b, f = _dir_size(render_dir)
                                    targets.append(("render_scratch", render_dir, b, f))
                            except OSError:
                                continue

        derived_mat_root = _resolve_root(self.settings.materials.cache_root)
        if derived_mat_root.is_dir():
            for entry in derived_mat_root.iterdir():
                if not entry.is_dir():
                    continue
                if entry.name not in known_materials:
                    try:
                        if now_ts - entry.stat().st_mtime > min_age_seconds:
                            b, f = _dir_size(entry)
                            targets.append(("orphan_derived_material", entry, b, f))
                    except OSError:
                        continue

        originals_root = _resolve_root(self.settings.materials.storage_root)
        if originals_root.is_dir():
            for acct_dir in originals_root.iterdir():
                if not acct_dir.is_dir():
                    continue
                for mat_dir in acct_dir.iterdir():
                    if not mat_dir.is_dir():
                        continue
                    if mat_dir.name not in known_materials:
                        try:
                            if now_ts - mat_dir.stat().st_mtime > min_age_seconds:
                                b, f = _dir_size(mat_dir)
                                targets.append(("orphan_original_material", mat_dir, b, f))
                        except OSError:
                            continue

        db_rows = {"expired_sessions": int(expired_sessions or 0), "expired_codes": int(expired_codes or 0)}
        return targets, db_rows

    def cleanup_preview(self) -> dict:
        targets, db_rows = self._safe_cleanup_targets()
        grouped: dict[str, dict] = {
            "render_scratch": {"key": "render_scratch", "label": "文档排版临时文件", "bytes": 0, "items": 0},
            "orphan_workspace": {"key": "orphan_workspace", "label": "已删会话残留草稿夹", "bytes": 0, "items": 0},
            "orphan_materials": {"key": "orphan_materials", "label": "已删附件孤立缓存", "bytes": 0, "items": 0},
            "expired_tokens": {
                "key": "expired_tokens",
                "label": "过期登录票据与验证码",
                "bytes": 0,
                "items": db_rows["expired_sessions"] + db_rows["expired_codes"],
            },
        }
        for kind, _path, b, _f in targets:
            bucket = (
                "render_scratch"
                if kind == "render_scratch"
                else "orphan_workspace"
                if kind in {"orphan_workspace", "orphan_skill_cache"}
                else "orphan_materials"
            )
            grouped[bucket]["bytes"] += b
            grouped[bucket]["items"] += 1

        total_bytes = sum(v["bytes"] for v in grouped.values())
        total_items = sum(v["items"] for v in grouped.values())
        return {
            "reclaimable_bytes": total_bytes,
            "reclaimable_items": total_items,
            "last_cleanup_at": self._last_cleanup_at,
            "last_cleanup_bytes": self._last_cleanup_bytes,
            "breakdown": [v for v in grouped.values() if v["items"] > 0 or v["bytes"] > 0],
        }

    def run_cleanup(self, *, actor_id: str | None = None, reason: str = "manual") -> dict:
        with self._lock:
            targets, _ = self._safe_cleanup_targets()
            cleaned_bytes = 0
            cleaned_dirs = 0
            allowed_roots = (
                _resolve_root(self.settings.execution.workspace_root),
                _resolve_root(self.settings.materials.cache_root),
                _resolve_root(self.settings.materials.storage_root),
            )
            for _kind, target, b, _f in targets:
                resolved = target.resolve()
                if not any(root in resolved.parents for root in allowed_roots):
                    continue
                if resolved.is_dir():
                    shutil.rmtree(resolved, ignore_errors=True)
                    cleaned_bytes += b
                    cleaned_dirs += 1

            now_dt = datetime.now(timezone.utc)
            with self.database.session() as session:
                res_sess = session.execute(
                    delete(SessionModel).where(
                        (SessionModel.expires_at < now_dt - timedelta(days=7))
                        | (
                            SessionModel.revoked_at.is_not(None)
                            & (SessionModel.revoked_at < now_dt - timedelta(days=30))
                        )
                    )
                )
                res_code = session.execute(
                    delete(EmailVerificationCodeModel).where(
                        EmailVerificationCodeModel.expires_at < now_dt - timedelta(days=1)
                    )
                )
                rows_deleted = int(res_sess.rowcount or 0) + int(res_code.rowcount or 0)
                if actor_id:
                    AdminService._add_audit(
                        session,
                        actor_id=actor_id,
                        target_account_id=None,
                        action="system.cleanup",
                        resource_type="system",
                        resource_id=reason,
                        details={
                            "cleaned_bytes": cleaned_bytes,
                            "cleaned_dirs": cleaned_dirs,
                            "cleaned_rows": rows_deleted,
                        },
                    )
                session.commit()

            self._last_cleanup_at = time.time()
            self._last_cleanup_bytes = cleaned_bytes
            with _DIR_LOCK:
                _DIR_CACHE.clear()
            return {
                "cleaned_bytes": cleaned_bytes,
                "cleaned_dirs": cleaned_dirs,
                "cleaned_rows": rows_deleted,
            }

    def list_backups(self) -> list[dict]:
        if not self.backups_dir.is_dir():
            return []
        items = []
        for entry in sorted(self.backups_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if entry.is_file() and _BACKUP_NAME_RE.fullmatch(entry.name):
                st = entry.stat()
                items.append(
                    {
                        "name": entry.name,
                        "size_bytes": st.st_size,
                        "modified_at": st.st_mtime,
                    }
                )
        return items[:30]

    def resolve_backup_path(self, filename: str) -> Path | None:
        if not _BACKUP_NAME_RE.fullmatch(filename or ""):
            return None
        root = self.backups_dir.resolve()
        candidate = (root / filename).resolve()
        if root not in candidate.parents or not candidate.is_file():
            return None
        return candidate

    def create_backup(
        self,
        *,
        actor_id: str | None = None,
        reason: str = "manual",
        keep_count: int = 7,
    ) -> dict:
        with self._lock:
            self.backups_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            zip_name = f"zhouxu-backup-{stamp}.zip"
            final_path = self.backups_dir / zip_name
            if final_path.exists():
                zip_name = f"zhouxu-backup-{stamp}-{int(time.time() * 1000) % 1000:03d}.zip"
                final_path = self.backups_dir / zip_name

            sources: list[tuple[str, Path]] = []
            if self.database_path and self.database_path.is_file():
                sources.append(("zhouxu.db", self.database_path))
            for db_name in _BACKUP_DB_NAMES:
                candidate = self.runtime_dir / db_name
                if candidate.is_file():
                    sources.append((db_name, candidate))

            manifest: dict[str, object] = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
                "databases": {},
            }
            with tempfile.TemporaryDirectory(prefix="zx-hot-backup-") as tmpdir:
                tmp_root = Path(tmpdir)
                tmp_zip = tmp_root / zip_name
                with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for arcname, src_path in sources:
                        staged_db = tmp_root / arcname
                        with (
                            closing(sqlite3.connect(f"{src_path.as_uri()}?mode=ro", uri=True, timeout=15)) as src_conn,
                            closing(sqlite3.connect(staged_db)) as dst_conn,
                        ):
                            src_conn.backup(dst_conn)
                            dst_conn.execute("PRAGMA journal_mode=DELETE")
                            if dst_conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                                raise RuntimeError(f"backup_integrity_failed:{arcname}")
                        sha256 = hashlib.sha256(staged_db.read_bytes()).hexdigest()
                        manifest["databases"][arcname] = {
                            "size_bytes": staged_db.stat().st_size,
                            "sha256": sha256,
                        }
                        zf.write(staged_db, arcname=arcname)
                    zf.writestr(
                        "manifest.json",
                        json.dumps(manifest, ensure_ascii=False, indent=2),
                    )
                shutil.move(str(tmp_zip), str(final_path))

            # Rotate older backups safely beyond keep_count
            existing = [
                p
                for p in sorted(self.backups_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
                if p.is_file() and _BACKUP_NAME_RE.fullmatch(p.name)
            ]
            for stale in existing[max(1, keep_count):]:
                stale.unlink(missing_ok=True)

            st = final_path.stat()
            if actor_id:
                with self.database.session() as session:
                    AdminService._add_audit(
                        session,
                        actor_id=actor_id,
                        target_account_id=None,
                        action="system.backup_created",
                        resource_type="system",
                        resource_id=zip_name,
                        details={"size_bytes": st.st_size, "reason": reason},
                    )
                    session.commit()
            return {"name": zip_name, "size_bytes": st.st_size, "modified_at": st.st_mtime}

    async def background_loop(self, stop_event: asyncio.Event, ops_service) -> None:
        """Low-frequency daily safe cleanup and rolling SQLite backup loop."""
        logger = logging.getLogger(__name__)
        # Wait 10 minutes after boot before first automated run.
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=600)
            return
        except TimeoutError:
            pass

        while not stop_event.is_set():
            try:
                cfg = await asyncio.to_thread(ops_service.load)
                if cfg.auto_maintenance_enabled:
                    cleaned = await asyncio.to_thread(self.run_cleanup, actor_id=None, reason="scheduled")
                    # Run daily rolling backup if latest backup is older than 20 hours (or none exists).
                    existing = self.list_backups()
                    need_backup = not existing or (time.time() - existing[0]["modified_at"]) > 20 * 3600
                    backup_info = None
                    if need_backup:
                        backup_info = await asyncio.to_thread(
                            self.create_backup,
                            actor_id=None,
                            reason="scheduled",
                            keep_count=cfg.backup_keep_days,
                        )
                    logger.info(
                        "scheduled_maintenance_done cleaned_bytes=%s backup=%s",
                        cleaned["cleaned_bytes"],
                        backup_info["name"] if backup_info else "skipped",
                    )
            except Exception as exc:
                logger.warning("scheduled_maintenance_failed err=%s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=24 * 3600)
            except TimeoutError:
                continue


__all__ = ["RuntimeMaintenanceService"]
