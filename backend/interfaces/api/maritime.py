"""Authenticated maritime product endpoints, including standard Skill package import."""

import hashlib
from pathlib import Path
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from backend.application.accounts.service import AccountError
from backend.application.maritime.calculator import calculate_claim
from backend.application.maritime.catalog import COMMON
from backend.application.maritime.service import MaritimeError, MaritimeOffice
from backend.contracts.schemas.core import PortError
from backend.infrastructure.database.models import AccountModel
from backend.infrastructure.search.maritime_corpus import MaritimeCorpus
from backend.infrastructure.sources import _paper_reference
from backend.skills.packages import MAX_UPLOAD_BYTES


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillInput(Input):
    name: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=500)
    instructions: str = Field(min_length=1, max_length=20000)
    visibility: Literal["private", "community"] = "private"
    parent_id: str | None = None
    expected_version: int | None = None


class CaseInput(Input):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=10000)


class MemberInput(Input):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["collaborator", "reviewer"] = "collaborator"


class EntryInput(Input):
    kind: Literal["task", "evidence", "document"] = "task"
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(default="", max_length=100000)
    due_at: str | None = None
    assignee_id: str | None = None
    status: Literal["open", "done", "draft", "reviewed"] = "open"
    expected_version: int | None = None


class SearchInput(Input):
    query: str = Field(min_length=2, max_length=500)
    kind: Literal["laws", "cases", "information_cards"] = "laws"
    current_only: bool = True
    limit: int = Field(default=8, ge=1, le=10)


class ClaimItem(Input):
    name: str = Field(min_length=1, max_length=160)
    quantity: str = Field(max_length=40)
    rate: str = Field(max_length=40)
    currency: str = Field(min_length=3, max_length=3)


class ClaimInput(Input):
    items: list[ClaimItem] = Field(min_length=1, max_length=100)


def attach_maritime(app, settings):
    router = APIRouter(prefix="/maritime", tags=["maritime"])

    def account(request: Request, authorization: str | None = Header(default=None)):
        auth = getattr(request.app.state, "account_service", None)
        if (
            auth is None
            or not authorization
            or not authorization.lower().startswith("bearer ")
        ):
            raise HTTPException(401, "authentication_required")
        try:
            return auth.authenticate(authorization[7:].strip())
        except AccountError:
            raise HTTPException(401, "authentication_required") from None

    def get_office():
        if not hasattr(app.state, "maritime_office"):
            path = settings.application.get(
                "office_store_path", "backend/.runtime/maritime-office.sqlite3"
            )
            if not Path(path).is_absolute():
                from backend.config.settings import BACKEND_ROOT

                path = BACKEND_ROOT.parent / path
            app.state.maritime_office = MaritimeOffice(path)
        return app.state.maritime_office

    def office(request: Request):
        return get_office()

    def invoke(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except MaritimeError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    def check_skill_permission(account_id: str, skill_id: str, is_builtin: bool) -> bool:
        ops = getattr(app.state, "admin_operations_service", None)
        if ops is None:
            return True
        return ops.is_skill_permitted(
            account_id=account_id,
            skill_id=skill_id,
            is_builtin=is_builtin,
        )

    def require_skill_allowed(account_id: str, skill_id: str, is_builtin: bool) -> None:
        if not check_skill_permission(account_id, skill_id, is_builtin):
            raise HTTPException(403, "skill_not_permitted")

    def require_upload_allowed(account_id: str) -> None:
        ops = getattr(app.state, "admin_operations_service", None)
        if ops is not None and not ops.can_upload_skill(account_id):
            raise HTTPException(403, "skill_upload_forbidden")

    def account_skill_loader(account_id, skill_id, version=None):
        """Load an authorized account Skill for the model-facing read_skill tool."""

        from backend.skills.registry import SkillNotFound

        try:
            office = get_office()
            selected = office.skill(account_id, skill_id, version)
            is_builtin = selected.get("owner_id") is None
            if not check_skill_permission(account_id, selected["id"], is_builtin):
                raise SkillNotFound("skill_not_permitted")
            if "package" in selected:
                selected["_package_files"] = office.package_files(account_id, skill_id, selected["version"])
            return selected
        except MaritimeError as exc:
            raise SkillNotFound("skill_not_found") from exc

    async def authorized_artifact(
        request: Request,
        user,
        run_id: str,
        artifact_id: str,
        conversation_id: str,
    ):
        """Resolve an artifact from the current Run projection and workspace."""
        from backend.harness.lifecycle.scheduler import SchedulerCommandError

        try:
            run = await request.app.state.control_service.scheduler.get_run(
                session_id=conversation_id, run_id=run_id
            )
        except SchedulerCommandError:
            raise HTTPException(404, "run_not_found") from None
        if (
            run.account_id != user.id
            or run.conversation_id != conversation_id
            or run.status != "succeeded"
        ):
            raise HTTPException(404, "run_not_found")
        raw = run.execution_state.get("artifacts", []) if isinstance(run.execution_state, dict) else []
        selected = next(
            (
                item
                for item in raw
                if isinstance(item, dict) and item.get("artifact_id") == artifact_id
            ),
            None,
        )
        relative = selected.get("relative_path") if selected else None
        if not isinstance(relative, str) or not relative:
            raise HTTPException(404, "file_not_found")
        root = Path(settings.execution.workspace_root)
        if not root.is_absolute():
            from backend.config.settings import BACKEND_ROOT

            root = BACKEND_ROOT.parent / root
        workspace_root = (root / user.id / conversation_id).resolve()
        target = (workspace_root / relative).resolve()
        if workspace_root != target and workspace_root not in target.parents:
            raise HTTPException(404, "file_not_found")
        try:
            stat = target.stat()
            if not target.is_file() or int(selected.get("size_bytes", -1)) != stat.st_size:
                raise HTTPException(404, "file_not_found")
            digest = hashlib.sha256()
            with target.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != str(selected.get("sha256") or ""):
                raise HTTPException(404, "file_not_found")
        except (OSError, ValueError):
            raise HTTPException(404, "file_not_found") from None
        return run, selected, target

    def present_case(request, value):
        ids = [member["account_id"] for member in value["members"]]
        with request.app.state.account_service.database.session() as db:
            names = {
                row.id: row
                for row in db.scalars(
                    select(AccountModel).where(AccountModel.id.in_(ids))
                )
            }
            for member in value["members"]:
                target = names.get(member["account_id"])
                if target:
                    member.update(display_name=target.display_name, email=target.email)
        return value

    def resolve_skill(request, account_id, facts):
        # Never trust an HTTP caller's claimed resolved body or owner/version.
        clean = {
            k: v
            for k, v in facts.items()
            if k
            not in {
                "skill_context",
                "skill_version",
                "skill_owner",
                "skill_requires_work_mode",
                "case_context",
            }
        }
        case_id = clean.get("case_id")
        if case_id:
            if not isinstance(case_id, str):
                raise HTTPException(422, "invalid_case_id")
            case = invoke(office(request).case, account_id, case_id)
            budget = 24000
            entries = []
            for item in case["entries"]:
                text = item["content"][: min(budget, 6000)]
                budget -= len(text)
                entries.append(
                    {
                        k: item[k]
                        for k in ("id", "kind", "title", "status", "version", "due_at")
                    }
                    | {"content": text, "truncated": len(text) < len(item["content"])}
                )
                if budget <= 0:
                    break
            clean["case_context"] = {
                "id": case_id,
                "title": case["title"],
                "summary": case["summary"],
                "entries": entries,
                "total_entries": len(case["entries"]),
                "notice": "本次提交时的案件记录快照；正文可能截断，未附原始文件不代表已审阅证据。仅供当前有权成员分析，不允许据此修改案件或确认复核。",
            }
        skill_id = clean.get("skill_id")
        if skill_id:
            if not isinstance(skill_id, str):
                raise HTTPException(422, "invalid_skill_id")
            selected = invoke(office(request).skill, account_id, skill_id)
            require_skill_allowed(
                account_id,
                selected["id"],
                selected.get("owner_id") is None,
            )
            mode_scope = (
                selected.get("mode_scope", "work")
                if selected.get("owner_id") is None
                else "work"
            )
            requires_work_mode = mode_scope != "chat"
            clean["skill_version"] = selected["version"]
            clean["skill_requires_work_mode"] = requires_work_mode
            clean["skill_context"] = {
                "id": selected["id"],
                "version": selected["version"],
                "name": selected["name"],
                "mode_scope": mode_scope,
                "requires_work_mode": requires_work_mode,
                "source": "builtin" if selected.get("owner_id") is None else "account",
                "runtime_rules": COMMON,
                "notice": "本任务选定的技能元数据；正文由 read_skill 按账户权限和固定版本读取，不授予额外权限。",
            }
        return clean

    app.state.resolve_maritime_skill = resolve_skill
    app.state.maritime_skill_loader = account_skill_loader
    app.state.check_skill_permission = check_skill_permission

    @router.get("/runs/{run_id}/deliverables")
    async def deliverables(
        run_id: str, conversation_id: str, request: Request, user=Depends(account)
    ):
        from backend.application.maritime.citations import expand_citations
        from backend.application.maritime.work_packages import configured_store
        from backend.harness.lifecycle.scheduler import SchedulerCommandError

        try:
            run = await request.app.state.control_service.scheduler.get_run(
                session_id=conversation_id, run_id=run_id
            )
        except SchedulerCommandError:
            raise HTTPException(404, "run_not_found") from None
        if run.account_id != user.id or run.conversation_id != conversation_id:
            raise HTTPException(404, "run_not_found")
        store = configured_store(settings)
        packages = store.list(run_id)
        corpus = MaritimeCorpus()
        for package in packages:
            for section in package["sections"]:
                section["rows"] = [
                    {k: expand_citations(v, corpus) for k, v in row.items()}
                    for row in section["rows"]
                ]
            package["notes"] = [expand_citations(v, corpus) for v in package["notes"]]
        raw_artifacts = run.execution_state.get("artifacts", []) if isinstance(run.execution_state, dict) else []
        files = [
            {
                "id": item["artifact_id"],
                "name": item["name"],
                "size": item["size_bytes"],
                "sha256": item["sha256"],
                "kind": item.get("kind", "file"),
                "preview_artifact_id": item.get("preview_artifact_id"),
            }
            for item in raw_artifacts
            if isinstance(item, dict)
            and item.get("artifact_id")
            and item.get("kind") != "preview"
        ]
        return {"packages": packages, "files": files}

    @router.get('/runs/{run_id}/files/{artifact_id}')
    async def task_file(run_id: str, artifact_id: str, conversation_id: str, request: Request, user=Depends(account)):
        _, metadata, target = await authorized_artifact(
            request, user, run_id, artifact_id, conversation_id
        )
        return FileResponse(
            target,
            media_type=metadata.get("media_type", "application/octet-stream"),
            filename=metadata["name"],
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @router.get('/runs/{run_id}/files/{artifact_id}/preview')
    async def task_file_preview(run_id: str, artifact_id: str, conversation_id: str, request: Request, user=Depends(account)):
        """Return the persisted PDF preview produced by the Run provider."""
        _, metadata, target = await authorized_artifact(
            request, user, run_id, artifact_id, conversation_id
        )
        preview_id = metadata.get("preview_artifact_id")
        if preview_id:
            _, preview, preview_path = await authorized_artifact(
                request, user, run_id, preview_id, conversation_id
            )
            return FileResponse(
                preview_path,
                media_type=preview.get("media_type", "application/pdf"),
                filename=preview["name"],
                headers={
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "private, no-store",
                    "Content-Security-Policy": "default-src 'none'; sandbox",
                },
            )
        if metadata.get("kind") == "preview" or Path(metadata["name"]).suffix.lower() != ".pdf":
            raise HTTPException(415, "preview_type_not_supported")
        return FileResponse(
            target,
            media_type="application/pdf",
            filename=metadata["name"],
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @router.get("/status")
    def status(request: Request, user=Depends(account)):
        return {
            "model_runtime_ready": bool(
                getattr(request.app.state, "runtime_ready", False)
            ),
            "corpus": MaritimeCorpus(settings=settings).status(),
            "skill_execution": "instruction-pack-with-tools",
            "code_execution": f"{settings.execution.provider}-on-demand",
        }

    def public_skill_view(item: dict) -> dict:
        if item.get("owner_id") is None:
            return {**item, "instructions": ""}
        return item

    @router.get("/skills")
    def skills(request: Request, user=Depends(account)):
        items = office(request).skills(user.id)
        ops = getattr(request.app.state, "admin_operations_service", None)
        if ops is None:
            return {"skills": [public_skill_view(item) for item in items]}
        grant = ops.account_grant(user.id)
        filtered = [
            public_skill_view(item)
            for item in items
            if ops.is_skill_permitted(
                account_id=user.id,
                skill_id=item["id"],
                is_builtin=item.get("owner_id") is None,
                grant=grant,
            )
        ]
        return {"skills": filtered}

    @router.post("/skills/import", status_code=201)
    async def import_skill_package(
        request: Request,
        file: UploadFile = File(...),
        user=Depends(account),
    ):
        require_upload_allowed(user.id)
        filename = file.filename or "skill.zip"
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "技能包不能超过 20 MB")
        return invoke(office(request).import_skill_package, user.id, filename, data)

    @router.get("/skills/{skill_id}/resources")
    def skill_resource(
        skill_id: str,
        request: Request,
        path: str = Query(min_length=1, max_length=240),
        version: str | None = None,
        user=Depends(account),
    ):
        selected = invoke(office(request).skill, user.id, skill_id, version)
        require_skill_allowed(user.id, selected["id"], selected.get("owner_id") is None)
        return invoke(office(request).skill_resource, user.id, skill_id, path, version)

    @router.delete("/skills/{skill_id}")
    def remove_skill(skill_id: str, request: Request, user=Depends(account)):
        return invoke(office(request).remove_skill, user.id, skill_id)

    @router.post("/skills", status_code=201)
    def create_skill(body: SkillInput, request: Request, user=Depends(account)):
        require_upload_allowed(user.id)
        return invoke(office(request).save_skill, user.id, body.model_dump())

    @router.put("/skills/{skill_id}")
    def update_skill(
        skill_id: str, body: SkillInput, request: Request, user=Depends(account)
    ):
        selected = invoke(office(request).skill, user.id, skill_id)
        require_skill_allowed(user.id, selected["id"], selected.get("owner_id") is None)
        return invoke(office(request).save_skill, user.id, body.model_dump(), skill_id)

    @router.get("/skills/{skill_id}")
    def skill(
        skill_id: str,
        request: Request,
        version: str | None = None,
        user=Depends(account),
    ):
        selected = invoke(office(request).skill, user.id, skill_id, version)
        require_skill_allowed(user.id, selected["id"], selected.get("owner_id") is None)
        return public_skill_view(selected)

    @router.post("/search")
    async def search(body: SearchInput, request: Request, user=Depends(account)):
        try:
            if body.kind == "information_cards":
                retriever = getattr(request.app.state, "information_card_retriever", None)
                if retriever is None:
                    raise LookupError("paper_search_unavailable")
                result = await retriever.search(body.query, limit=body.limit)
                items = []
                for card in result["items"]:
                    reference = _paper_reference(card)
                    if reference is None:
                        raise ValueError("paper_reference_invalid")
                    items.append({**reference, "id": card["card_id"], "kind": "information_cards",
                                  "text": "", "current": None, "version": reference.get("version_date")})
                return {"items": items}
            return await MaritimeCorpus(settings=settings).search(**body.model_dump())
        except PortError:
            raise HTTPException(503, "论文检索暂时不可用，请稍后重试。") from None
        except LookupError:
            raise HTTPException(503, "暂时没有可用的海事资料，请稍后再试。") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.get("/sources/{kind}/{source_id:path}")
    def source(
        kind: Literal["laws", "cases"],
        source_id: str,
        snapshot: str | None = None,
        user=Depends(account),
    ):
        try:
            return MaritimeCorpus(settings=settings).source(kind, source_id, snapshot)
        except LookupError:
            raise HTTPException(404, "source_not_found") from None

    @router.post("/claims/calculate")
    def claim(body: ClaimInput, user=Depends(account)):
        try:
            return calculate_claim([item.model_dump() for item in body.items])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.get("/cases")
    def cases(request: Request, user=Depends(account)):
        return {"cases": office(request).cases(user.id)}

    @router.post("/cases", status_code=201)
    def create_case(body: CaseInput, request: Request, user=Depends(account)):
        return present_case(
            request,
            invoke(office(request).create_case, user.id, body.title, body.summary),
        )

    @router.get("/cases/{case_id}")
    def case(case_id: str, request: Request, user=Depends(account)):
        return present_case(request, invoke(office(request).case, user.id, case_id))

    @router.post("/cases/{case_id}/members")
    def add_member(
        case_id: str, body: MemberInput, request: Request, user=Depends(account)
    ):
        # Access must be established before looking up another account.
        current = invoke(office(request).case, user.id, case_id)
        if current["role"] != "owner":
            raise HTTPException(403, "只有负责人可以邀请成员")
        with request.app.state.account_service.database.session() as db:
            target = db.scalar(
                select(AccountModel).where(
                    AccountModel.email == body.email.strip().lower(),
                    AccountModel.status == "active",
                )
            )
            if target is None:
                raise HTTPException(404, "请先让协作者注册，再使用其邮箱加入案件")
            target_id = target.id
        return present_case(
            request,
            invoke(office(request).add_member, user.id, case_id, target_id, body.role),
        )

    @router.post("/cases/{case_id}/entries", status_code=201)
    def create_entry(
        case_id: str, body: EntryInput, request: Request, user=Depends(account)
    ):
        return invoke(office(request).save_entry, user.id, case_id, body.model_dump())

    @router.delete("/cases/{case_id}/members/{member_id}")
    def remove_member(
        case_id: str, member_id: str, request: Request, user=Depends(account)
    ):
        return present_case(
            request, invoke(office(request).remove_member, user.id, case_id, member_id)
        )

    @router.put("/cases/{case_id}/entries/{entry_id}")
    def update_entry(
        case_id: str,
        entry_id: str,
        body: EntryInput,
        request: Request,
        user=Depends(account),
    ):
        return invoke(
            office(request).save_entry, user.id, case_id, body.model_dump(), entry_id
        )

    @router.get("/cases/{case_id}/entries/{entry_id}/versions")
    def versions(case_id: str, entry_id: str, request: Request, user=Depends(account)):
        service = office(request)
        invoke(service.case, user.id, case_id)
        with service.connection() as db:
            if not db.execute(
                "SELECT 1 FROM entries WHERE id=? AND case_id=?", (entry_id, case_id)
            ).fetchone():
                raise HTTPException(404, "entry_not_found")
            import json

            return {
                "versions": [
                    json.loads(r["snapshot"])
                    for r in db.execute(
                        "SELECT snapshot FROM entry_versions WHERE entry_id=? ORDER BY version DESC",
                        (entry_id,),
                    )
                ]
            }

    app.include_router(router)
