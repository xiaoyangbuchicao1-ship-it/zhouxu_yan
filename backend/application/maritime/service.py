"""Account-scoped application store with append-only versions and audit receipts."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.application.maritime.catalog import READY_BUILTIN_METADATA
from backend.skills.packages import (
    PackageError,
    create_package_schema,
    read_package,
    safe_resource_path,
)
from backend.skills.registry import default_skill_registry


class MaritimeError(Exception):
    def __init__(self, message, status=400):
        self.status = status
        super().__init__(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def registered_builtin_skills():
    """Project declarative registry Skills into the account-facing shape."""

    registry = default_skill_registry()
    result = []
    for descriptor in registry.descriptors():
        method = registry.method(descriptor.name)
        ready_meta = READY_BUILTIN_METADATA.get(descriptor.skill_id)
        result.append(
            {
                "id": descriptor.skill_id,
                "name": descriptor.name,
                "display_name": ready_meta["display_name"] if ready_meta else descriptor.name,
                "starter_prompt": ready_meta["starter_prompt"] if ready_meta else "",
                "status": "ready",
                "mode_scope": ready_meta.get("mode_scope") if ready_meta else descriptor.mode_scope,
                "deliverable_tag": (
                    ready_meta.get("deliverable_tag")
                    if ready_meta
                    else ("只读检索" if descriptor.mode_scope == "chat" else "工作模式")
                ),
                "summary": (ready_meta.get("summary") if ready_meta else None) or descriptor.description,
                "instructions": registry.read(descriptor.name),
                "version": descriptor.version,
                "owner_id": None,
                "visibility": "builtin",
                "provenance": (
                    "舟序海事研究方法 · 只读检索流程"
                    if descriptor.mode_scope == "chat"
                    else "舟序工作模式专业交付规范"
                ),
                **{
                    key: value
                    for key, value in method.items()
                    if key not in {"id", "name", "summary", "version", "display_name", "starter_prompt", "status", "mode_scope", "deliverable_tag"}
                },
            }
        )
    return result


class MaritimeOffice:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            existing = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='skills'").fetchone()
            if existing:
                required = {"skill_packages", "skill_package_files", "skill_installations"}
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not required.issubset(tables):
                    raise RuntimeError("skill_package_migration_required: run backend.infrastructure.persistence.migrate_skill_packages before starting")
            db.executescript("""
              CREATE TABLE IF NOT EXISTS skills(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                name TEXT NOT NULL, summary TEXT NOT NULL, instructions TEXT NOT NULL,
                version INTEGER NOT NULL, visibility TEXT NOT NULL, parent_id TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS skill_versions(skill_id TEXT, version INTEGER,
                snapshot TEXT NOT NULL, PRIMARY KEY(skill_id, version));
            """)
            if not existing:
                create_package_schema(db)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def skills(self, account_id):
        with self.connection() as db:
            custom = [
                self._with_package(db, dict(r))
                for r in db.execute(
                    "SELECT s.* FROM skills s LEFT JOIN skill_installations i ON i.skill_id=s.id "
                    "WHERE s.owner_id=? AND (i.active IS NULL OR i.active=1) ORDER BY s.updated_at DESC",
                    (account_id,),
                )
            ]
        return registered_builtin_skills() + custom

    def skill(self, account_id, skill_id, version=None):
        for item in registered_builtin_skills():
            if item["id"] == skill_id:
                if version is None or str(version) == str(item["version"]):
                    return item
                raise MaritimeError("技能版本不存在", 404)
        if version is not None:
            try:
                version = int(version)
            except (TypeError, ValueError):
                raise MaritimeError("技能版本不存在", 404) from None
        with self.connection() as db:
            row = db.execute(
                "SELECT s.* FROM skills s LEFT JOIN skill_installations i ON i.skill_id=s.id "
                "WHERE s.id=? AND s.owner_id=? "
                "AND (? IS NOT NULL OR i.active IS NULL OR i.active=1)",
                (skill_id, account_id, version),
            ).fetchone()
            if row and version is not None:
                old = db.execute(
                    "SELECT snapshot FROM skill_versions WHERE skill_id=? AND version=?",
                    (skill_id, version),
                ).fetchone()
                if old:
                    return self._with_package(db, json.loads(old["snapshot"]))
                row = None
            if not row:
                raise MaritimeError("技能不存在或无权使用", 404)
            return self._with_package(db, dict(row))

    @staticmethod
    def _with_package(db, row):
        package = db.execute("SELECT manifest FROM skill_packages WHERE skill_id=? AND version=?", (row["id"], row["version"])).fetchone()
        if package:
            row["package"] = json.loads(package["manifest"])
        return row

    def import_skill_package(self, account_id, filename, data):
        try:
            package = read_package(filename, data)
        except PackageError as exc:
            raise MaritimeError(str(exc)) from exc
        row = dict(
            id=uuid4().hex, owner_id=account_id, name=package.metadata.name,
            summary=package.metadata.description, instructions=package.instructions,
            version=1, visibility="private", parent_id=None, created_at=now(), updated_at=now(),
        )
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO skills VALUES (:id,:owner_id,:name,:summary,:instructions,:version,:visibility,:parent_id,:created_at,:updated_at)", row)
            db.execute("INSERT INTO skill_versions VALUES (?,?,?)", (row["id"], 1, json.dumps(row, ensure_ascii=False)))
            db.execute("INSERT INTO skill_packages VALUES (?,?,?)", (row["id"], 1, json.dumps(package.manifest, ensure_ascii=False)))
            db.executemany("INSERT INTO skill_package_files VALUES (?,?,?,?,?)", [
                (row["id"], 1, path, content, hashlib.sha256(content).hexdigest())
                for path, content in package.files.items()
            ])
            db.execute("INSERT INTO skill_installations VALUES (?,1)", (row["id"],))
        return {**row, "package": package.manifest}

    def package_files(self, account_id, skill_id, version=None):
        selected = self.skill(account_id, skill_id, version)
        if "package" not in selected:
            return {}
        with self.connection() as db:
            rows = db.execute("SELECT path,content,sha256 FROM skill_package_files WHERE skill_id=? AND version=? ORDER BY path", (skill_id, selected["version"])).fetchall()
        files = {}
        expected = {item["path"]: item for item in selected["package"]["files"]}
        for row in rows:
            content = bytes(row["content"])
            if row["path"] not in expected or hashlib.sha256(content).hexdigest() != expected[row["path"]]["sha256"] or len(content) != expected[row["path"]]["size_bytes"]:
                raise MaritimeError("技能包文件校验失败，请重新上传", 409)
            files[row["path"]] = content
        if files.keys() != expected.keys():
            raise MaritimeError("技能包文件不完整，请重新上传", 409)
        return files

    def skill_resource(self, account_id, skill_id, path, version=None):
        try:
            safe_resource_path(path)
        except PackageError as exc:
            raise MaritimeError(str(exc)) from exc
        selected = self.skill(account_id, skill_id, version)
        if selected.get("owner_id") is None:
            registry = default_skill_registry()
            from backend.skills.registry import SkillNotFound
            try:
                content = registry.read_resource(skill_id, path)
            except SkillNotFound as exc:
                raise MaritimeError("技能资源不存在", 404) from exc
            data = content.encode("utf-8")
        else:
            files = self.package_files(account_id, skill_id, selected["version"])
            if path not in files:
                raise MaritimeError("技能资源不存在", 404)
            data = files[path]
            try:
                content = data.decode("utf-8") if len(data) <= 128 * 1024 else None
            except UnicodeError:
                content = None
        return {"path": path, "content": content, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}

    @staticmethod
    def _delete_skill_rows(db, skill_id):
        for table in ("skill_package_files", "skill_packages", "skill_installations", "skill_versions", "skills"):
            db.execute(f"DELETE FROM {table} WHERE {'id' if table == 'skills' else 'skill_id'}=?", (skill_id,))

    def remove_skill(self, account_id, skill_id):
        """Hide one of the caller's skills.

        The package and version rows stay: a completed task keeps the exact
        skill version it used.  Only an administrator can delete them for good.
        """

        with self.connection() as db:
            result = db.execute(
                "UPDATE skill_installations SET active=0 WHERE skill_id IN "
                "(SELECT id FROM skills WHERE id=? AND owner_id=?) AND active=1",
                (skill_id, account_id),
            )
            if not result.rowcount:
                raise MaritimeError("技能不存在或无权移除", 404)
        return {"status": "removed"}

    def uploaded_skills(self):
        """Every uploaded skill, for the administrator overview."""

        with self.connection() as db:
            rows = db.execute(
                "SELECT s.id,s.owner_id,s.name,s.summary,s.version,s.visibility,s.created_at,s.updated_at,"
                "COALESCE(i.active,1) AS active,p.manifest "
                "FROM skills s LEFT JOIN skill_installations i ON i.skill_id=s.id "
                "LEFT JOIN skill_packages p ON p.skill_id=s.id AND p.version=s.version "
                "ORDER BY s.updated_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            files = json.loads(row["manifest"])["files"] if row["manifest"] else []
            result.append(
                {
                    "id": row["id"],
                    "owner_id": row["owner_id"],
                    "name": row["name"],
                    "summary": row["summary"],
                    "version": row["version"],
                    "active": bool(row["active"]),
                    "file_count": len(files),
                    "size_bytes": sum(int(item.get("size_bytes", 0)) for item in files),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return result

    def admin_remove_skill(self, skill_id):
        """Permanently delete any uploaded skill, including its history; returns what was removed."""

        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT owner_id,name FROM skills WHERE id=?", (skill_id,)).fetchone()
            if not row:
                raise MaritimeError("技能不存在", 404)
            self._delete_skill_rows(db, skill_id)
        return {"owner_id": row["owner_id"], "name": row["name"]}

    def admin_remove_account_skills(self, account_id: str) -> int:
        """Permanently delete every uploaded skill owned by a deleted account."""

        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id FROM skills WHERE owner_id=?", (account_id,)).fetchall()
            for row in rows:
                self._delete_skill_rows(db, row["id"])
        return len(rows)
