"""Account-scoped application store with append-only versions and audit receipts."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.application.maritime.catalog import (
    READY_BUILTIN_METADATA,
    builtin_skills,
    historical_builtin,
)
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
    known = {item["id"] for item in builtin_skills()}
    result = []
    for descriptor in registry.descriptors():
        if descriptor.skill_id in known:
            continue
        method = registry.method(descriptor.name)
        ready_meta = READY_BUILTIN_METADATA.get(descriptor.skill_id)
        result.append(
            {
                "id": descriptor.skill_id,
                "name": descriptor.name,
                "display_name": ready_meta["display_name"] if ready_meta else descriptor.name,
                "starter_prompt": ready_meta["starter_prompt"] if ready_meta else "",
                "status": "ready" if ready_meta else "coming_soon",
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
              CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                title TEXT NOT NULL, summary TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS members(case_id TEXT NOT NULL REFERENCES cases(id),
                account_id TEXT NOT NULL, role TEXT NOT NULL, PRIMARY KEY(case_id,account_id));
              CREATE TABLE IF NOT EXISTS entries(id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
                kind TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, due_at TEXT,
                assignee_id TEXT, status TEXT NOT NULL, version INTEGER NOT NULL,
                author_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS entry_versions(entry_id TEXT, version INTEGER,
                snapshot TEXT NOT NULL, PRIMARY KEY(entry_id,version));
              CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, case_id TEXT,
                actor_id TEXT NOT NULL, action TEXT NOT NULL, object_id TEXT NOT NULL,
                created_at TEXT NOT NULL);
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
                    "WHERE (s.owner_id=? OR s.visibility='community') AND (i.active IS NULL OR i.active=1) ORDER BY s.updated_at DESC",
                    (account_id,),
                )
            ]
        return builtin_skills() + registered_builtin_skills() + custom

    def skill(self, account_id, skill_id, version=None):
        for item in builtin_skills() + registered_builtin_skills():
            if item["id"] == skill_id:
                if version is None or str(version) == str(item["version"]):
                    return item
                old = historical_builtin(skill_id, version)
                if old:
                    return old
                raise MaritimeError("技能版本不存在", 404)
        if version is not None:
            try:
                version = int(version)
            except (TypeError, ValueError):
                raise MaritimeError("技能版本不存在", 404) from None
        with self.connection() as db:
            row = db.execute(
                "SELECT s.* FROM skills s LEFT JOIN skill_installations i ON i.skill_id=s.id "
                "WHERE s.id=? AND (s.owner_id=? OR s.visibility='community') "
                "AND (? IS NOT NULL OR i.active IS NULL OR i.active=1)",
                (skill_id, account_id, version),
            ).fetchone()
            if row and version is not None:
                if row["owner_id"] != account_id and version != row["version"]:
                    raise MaritimeError("历史技能版本仅作者可见", 404)
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

    def remove_skill(self, account_id, skill_id):
        with self.connection() as db:
            result = db.execute("UPDATE skill_installations SET active=0 WHERE skill_id IN (SELECT id FROM skills WHERE id=? AND owner_id=?) AND active=1", (skill_id, account_id))
            if not result.rowcount:
                raise MaritimeError("技能不存在或无权移除", 404)
        return {"status": "removed"}

    def save_skill(self, account_id, value, skill_id=None):
        fields = {
            k: str(value.get(k, "")).strip()
            for k in ("name", "summary", "instructions")
        }
        if (
            not all(fields.values())
            or len(fields["name"]) > 80
            or len(fields["summary"]) > 500
            or len(fields["instructions"]) > 20000
        ):
            raise MaritimeError("请填写名称、简介和完整技能说明，并遵守长度限制")
        visibility = value.get("visibility", "private")
        if visibility not in {"private", "community"}:
            raise MaritimeError("无效的发布范围")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if skill_id:
                if db.execute("SELECT 1 FROM skill_installations WHERE skill_id=?", (skill_id,)).fetchone():
                    raise MaritimeError("请重新上传完整技能包，不支持只改包内说明", 409)
                old = db.execute(
                    "SELECT * FROM skills WHERE id=? AND owner_id=?",
                    (skill_id, account_id),
                ).fetchone()
                if not old:
                    raise MaritimeError("只能编辑自己创建的技能", 404)
                if value.get("expected_version") != old["version"]:
                    raise MaritimeError("技能已被更新，请重新载入后再保存", 409)
                row = {
                    **dict(old),
                    **fields,
                    "visibility": visibility,
                    "version": old["version"] + 1,
                    "updated_at": now(),
                }
                db.execute(
                    "UPDATE skills SET name=:name,summary=:summary,instructions=:instructions,visibility=:visibility,version=:version,updated_at=:updated_at WHERE id=:id",
                    row,
                )
            else:
                parent = value.get("parent_id")
                if parent:
                    self.skill(account_id, parent)
                row = dict(
                    id=uuid4().hex,
                    owner_id=account_id,
                    **fields,
                    visibility=visibility,
                    version=1,
                    parent_id=parent,
                    created_at=now(),
                    updated_at=now(),
                )
                db.execute(
                    "INSERT INTO skills VALUES (:id,:owner_id,:name,:summary,:instructions,:version,:visibility,:parent_id,:created_at,:updated_at)",
                    row,
                )
            db.execute(
                "INSERT INTO skill_versions VALUES (?,?,?)",
                (row["id"], row["version"], json.dumps(row, ensure_ascii=False)),
            )
        return row

    def _access(self, db, account_id, case_id, *, owner=False):
        row = db.execute(
            "SELECT c.*,m.role FROM cases c JOIN members m ON c.id=m.case_id WHERE c.id=? AND m.account_id=?",
            (case_id, account_id),
        ).fetchone()
        if not row:
            raise MaritimeError("案件不存在或无权访问", 404)
        if owner and row["role"] != "owner":
            raise MaritimeError("只有案件负责人可以管理协作者", 403)
        return dict(row)

    def _audit(self, db, account_id, case_id, action, object_id):
        db.execute(
            "INSERT INTO audit VALUES (?,?,?,?,?,?)",
            (uuid4().hex, case_id, account_id, action, object_id, now()),
        )
        db.execute("UPDATE cases SET updated_at=? WHERE id=?", (now(), case_id))

    def cases(self, account_id):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT c.*,m.role FROM cases c JOIN members m ON c.id=m.case_id WHERE m.account_id=? ORDER BY c.updated_at DESC",
                    (account_id,),
                )
            ]

    def create_case(self, account_id, title, summary=""):
        title = title.strip()
        if not title or len(title) > 160 or len(summary) > 10000:
            raise MaritimeError("案件名称为必填项，且应不超过 160 字")
        case_id = uuid4().hex
        with self.connection() as db:
            db.execute(
                "INSERT INTO cases VALUES (?,?,?,?,?,?,?)",
                (case_id, account_id, title, summary, "active", now(), now()),
            )
            db.execute(
                "INSERT INTO members VALUES (?,?,?)", (case_id, account_id, "owner")
            )
            self._audit(db, account_id, case_id, "case.created", case_id)
        return self.case(account_id, case_id)

    def case(self, account_id, case_id):
        with self.connection() as db:
            row = self._access(db, account_id, case_id)
            row["members"] = [
                dict(r)
                for r in db.execute(
                    "SELECT account_id,role FROM members WHERE case_id=?", (case_id,)
                )
            ]
            row["entries"] = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM entries WHERE case_id=? ORDER BY created_at DESC",
                    (case_id,),
                )
            ]
            row["audit"] = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM audit WHERE case_id=? ORDER BY created_at DESC LIMIT 200",
                    (case_id,),
                )
            ]
            return row

    def add_member(self, account_id, case_id, target_id, role):
        if role not in {"collaborator", "reviewer"}:
            raise MaritimeError("协作角色只能是协作者或复核人")
        with self.connection() as db:
            self._access(db, account_id, case_id, owner=True)
            if db.execute(
                "SELECT 1 FROM members WHERE case_id=? AND account_id=? AND role='owner'",
                (case_id, target_id),
            ).fetchone():
                raise MaritimeError("不能变更负责人角色")
            db.execute(
                "INSERT INTO members VALUES (?,?,?) ON CONFLICT(case_id,account_id) DO UPDATE SET role=excluded.role",
                (case_id, target_id, role),
            )
            self._audit(db, account_id, case_id, "member." + role, target_id)
        return self.case(account_id, case_id)

    def save_entry(self, account_id, case_id, value, entry_id=None):
        kind = value.get("kind", "task")
        status = value.get("status", "open" if kind == "task" else "draft")
        if kind not in {"task", "evidence", "document"} or status not in {
            "open",
            "done",
            "draft",
            "reviewed",
        }:
            raise MaritimeError("不支持的事项类型或状态")
        if (kind == "task" and status not in {"open", "done"}) or (
            kind != "task" and status not in {"draft", "reviewed"}
        ):
            raise MaritimeError("状态与事项类型不匹配")
        title, content = (
            str(value.get("title", "")).strip(),
            str(value.get("content", "")),
        )
        if not title or len(title) > 160 or len(content) > 100000:
            raise MaritimeError("请填写标题，并遵守内容长度限制")
        due = value.get("due_at") or None
        if due:
            try:
                datetime.strptime(due, "%Y-%m-%d")
            except (ValueError, TypeError):
                raise MaritimeError(
                    "日期应为有效的 YYYY-MM-DD；法定期限需要人工核定"
                ) from None
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            access = self._access(db, account_id, case_id)
            if status == "reviewed" and access["role"] not in {"owner", "reviewer"}:
                raise MaritimeError("需要案件负责人或复核人确认", 403)
            assignee = value.get("assignee_id") or None
            if (
                assignee
                and not db.execute(
                    "SELECT 1 FROM members WHERE case_id=? AND account_id=?",
                    (case_id, assignee),
                ).fetchone()
            ):
                raise MaritimeError("负责人必须是本案件的成员")
            if entry_id:
                old = db.execute(
                    "SELECT * FROM entries WHERE id=? AND case_id=?",
                    (entry_id, case_id),
                ).fetchone()
                if not old:
                    raise MaritimeError("事项不存在", 404)
                if (
                    old["kind"] != kind
                    or value.get("expected_version") != old["version"]
                ):
                    raise MaritimeError("事项版本已变化，请重新载入", 409)
                # Editing a reviewed body invalidates its signoff unless a reviewer signs again.
                if access["role"] == "collaborator" and old["status"] == "reviewed":
                    status = "draft"
                row = {
                    **dict(old),
                    "title": title,
                    "content": content,
                    "due_at": due,
                    "assignee_id": assignee,
                    "status": status,
                    "version": old["version"] + 1,
                    "updated_at": now(),
                }
                db.execute(
                    "UPDATE entries SET title=:title,content=:content,due_at=:due_at,assignee_id=:assignee_id,status=:status,version=:version,updated_at=:updated_at WHERE id=:id",
                    row,
                )
            else:
                row = dict(
                    id=uuid4().hex,
                    case_id=case_id,
                    kind=kind,
                    title=title,
                    content=content,
                    due_at=due,
                    assignee_id=assignee,
                    status=status,
                    version=1,
                    author_id=account_id,
                    created_at=now(),
                    updated_at=now(),
                )
                db.execute(
                    "INSERT INTO entries VALUES (:id,:case_id,:kind,:title,:content,:due_at,:assignee_id,:status,:version,:author_id,:created_at,:updated_at)",
                    row,
                )
            db.execute(
                "INSERT INTO entry_versions VALUES (?,?,?)",
                (row["id"], row["version"], json.dumps(row, ensure_ascii=False)),
            )
            self._audit(db, account_id, case_id, f"{kind}.{status}", row["id"])
            return row

    def remove_member(self, account_id, case_id, target_id):
        with self.connection() as db:
            self._access(db, account_id, case_id, owner=True)
            if target_id == account_id:
                raise MaritimeError("不能移除案件负责人")
            removed = db.execute(
                "DELETE FROM members WHERE case_id=? AND account_id=?",
                (case_id, target_id),
            ).rowcount
            if removed != 1:
                raise MaritimeError("案件成员不存在", 404)
            self._audit(db, account_id, case_id, "member.removed", target_id)
        return self.case(account_id, case_id)
