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

from backend.application.accounts.service import AccountError
from backend.application.maritime.service import MaritimeError, MaritimeOffice
from backend.contracts.schemas.core import PortError
from backend.infrastructure.search.maritime_corpus import MaritimeCorpus
from backend.infrastructure.sources import _paper_reference
from backend.skills.packages import MAX_UPLOAD_BYTES


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


def office_for(app, settings) -> MaritimeOffice:
    """The one skill/office store shared by the user routes and the admin routes."""

    if not hasattr(app.state, "maritime_office"):
        path = settings.application.get(
            "office_store_path", "backend/.runtime/maritime-office.sqlite3"
        )
        if not Path(path).is_absolute():
            from backend.config.settings import BACKEND_ROOT

            path = BACKEND_ROOT.parent / path
        app.state.maritime_office = MaritimeOffice(path)
    return app.state.maritime_office


class SearchInput(Input):
    query: str = Field(min_length=2, max_length=500)
    kind: Literal["laws", "cases", "information_cards"] = "laws"
    current_only: bool = True
    limit: int = Field(default=8, ge=1, le=10)


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
        return office_for(app, settings)

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
            }
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
        from backend.harness.lifecycle.scheduler import SchedulerCommandError

        try:
            run = await request.app.state.control_service.scheduler.get_run(
                session_id=conversation_id, run_id=run_id
            )
        except SchedulerCommandError:
            raise HTTPException(404, "run_not_found") from None
        if run.account_id != user.id or run.conversation_id != conversation_id:
            raise HTTPException(404, "run_not_found")
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
        return {"files": files}

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

    def public_skill_view(item: dict) -> dict:
        if item.get("owner_id") is None:
            return {**item, "instructions": ""}
        return item

    @router.get("/skills")
    def skills(request: Request, user=Depends(account)):
        items = office(request).skills(user.id)
        ops = getattr(request.app.state, "admin_operations_service", None)
        if ops is None:
            return {"skills": [public_skill_view(item) for item in items], "can_upload_skill": True}
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
        return {"skills": filtered, "can_upload_skill": bool(grant["can_upload_skill"])}

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
        from time import perf_counter

        from backend.infrastructure.telemetry import metrics as telemetry

        started = perf_counter()
        outcome = "error"
        try:
            result = await _search(body, request)
            outcome = "ok" if result.get("items") else "empty"
            return result
        finally:
            store = telemetry.get_store()
            if store is not None:
                store.record_tool(
                    tool="用户检索页",
                    source={"laws": "laws", "cases": "cases", "information_cards": "papers"}[body.kind],
                    outcome=outcome,
                    duration_ms=int((perf_counter() - started) * 1000),
                    account_id=user.id,
                )

    async def _search(body: SearchInput, request: Request):
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
                        # One unreadable card must not hide the other results.
                        continue
                    items.append({**reference, "id": card["card_id"], "kind": "information_cards",
                                  "text": "", "current": None, "version": reference.get("version_date")})
                return {"items": items}
            return await MaritimeCorpus(settings=settings).search(**body.model_dump())
        except PortError:
            what = "论文检索" if body.kind == "information_cards" else "法规和案例检索"
            raise HTTPException(503, f"{what}暂时不可用，请稍后重试。") from None
        except LookupError:
            raise HTTPException(503, "暂时没有可用的海事资料，请稍后再试。") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    app.include_router(router)
