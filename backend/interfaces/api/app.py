"""FastAPI adapter for the Harness control plane."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from time import monotonic
from typing import AsyncIterator

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from backend.application.accounts.service import (
    AccountError,
    AccountPrincipal,
    AccountService,
)
from backend.application.workspace.conversations import WorkspaceError, WorkspaceService
from backend.application.workspace.materials import (
    MaterialService,
    MaterialServiceError,
)
from backend.application.workspace.run_projection import RunProjection
from backend.bootstrap import open_harnesses
from backend.config.settings import load_settings
from backend.contracts.events.runtime import RunEvent
from backend.contracts.events.streaming import StreamEvent, visible_text
from backend.contracts.harness.artifacts import ArtifactReference
from backend.contracts.harness.lifecycle import RunEnvelope
from backend.contracts.modules.loop import RunInput
from backend.contracts.modules.materials import MaterialReadInput
from backend.contracts.modules.sources import SourceReference
from backend.contracts.schemas.core import Error, PortError
from backend.harness.lifecycle.recovery import (
    CHECKPOINT_CODES,
    recovery_for_run,
    recovery_for_state,
)
from backend.infrastructure.database.connection import Database
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.parsing.read_view import read_view
from backend.infrastructure.parsing.registry import MaterialParserRegistry
from backend.infrastructure.persistence.run_store import SQLiteRunStore
from backend.infrastructure.sources import CorpusSourceRepository
from backend.interfaces.api.contracts import (
    AccountView,
    AuthCredentialsRequest,
    AuthSessionView,
    CreateConversationRequest,
    CreateProjectRequest,
    InputRequestResponse,
    MaterialUploadResponse,
    MoveConversationRequest,
    ResumeRunRequest,
    RunCommandResponse,
    RunControlRequest,
    SessionResponse,
    SteerRunRequest,
    SubmitRunRequest,
    UpdateConversationRequest,
    UpdateProjectRequest,
    WorkbenchErrorView,
    WorkbenchInputRequestResponse,
    WorkbenchInputRequestView,
    WorkbenchResumeRequest,
    WorkbenchRunCommandResponse,
    WorkbenchRunControlRequest,
    WorkbenchRunPayload,
    WorkbenchRunView,
    WorkbenchSnapshotResponse,
    WorkbenchSteerRequest,
    WorkbenchSubmitRunRequest,
    WorkspaceConversationDetailResponse,
    WorkspaceConversationListResponse,
    WorkspaceConversationResponse,
    WorkspaceConversationView,
    WorkspaceMaterialListResponse,
    WorkspaceMaterialView,
    WorkspaceMessageView,
    WorkspacePassageListResponse,
    WorkspacePassageView,
    WorkspaceProjectListResponse,
    WorkspaceProjectView,
    WorkspaceSourceResponse,
)
from backend.interfaces.api.control_plane import (
    HarnessControlService,
    SchedulerCommandError,
    UnavailableRunExecutor,
)


def public_error_message(error: Error | None) -> str:
    """Map a stream failure to a short user-facing status message."""

    code = getattr(error, "code", None)
    if code in CHECKPOINT_CODES:
        return "已保留当前进度，可以继续处理。"
    return "这次处理没有完成。"


class EventHub(MemoryEventSink):
    """Bounded process-local runtime metadata used by the NDJSON adapter."""

    def __init__(self, max_events: int = 5000, store=None):
        super().__init__()
        self.max_events, self.store = max_events, store

    def emit(self, event: RunEvent) -> None:
        super().emit(event)
        if len(self.events) > self.max_events:
            del self.events[: len(self.events) - self.max_events]

    def emit_stream(self, event: StreamEvent) -> None:
        if self.store:
            event = self.store.append_event(event)
            self._stream_sequences[event.run_id] = event.sequence
            self.stream_events.append(event)
        else:
            super().emit_stream(event)
        if len(self.stream_events) > self.max_events:
            del self.stream_events[: len(self.stream_events) - self.max_events]


    def visible(self, run_id):
        if self.store:
            return self.store.visible(run_id)
        items = super().stream_events_since(run_id=run_id)
        return visible_text(items), items[-1].sequence if items else 0

    def stream_events_since(self, *, run_id=None, after_sequence=0):
        if self.store and run_id:
            return self.store.stream_events_since(run_id=run_id, after_sequence=after_sequence)
        return super().stream_events_since(run_id=run_id, after_sequence=after_sequence)


def _command_error(exc: SchedulerCommandError) -> HTTPException:
    message = str(exc)
    status = (
        404
        if message in {"session not found", "run not found", "input request not found"}
        else 409
    )
    return HTTPException(status_code=status, detail=message)


def _json_line(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def create_app(
    *,
    control_service: HarnessControlService | None = None,
    account_service: AccountService | None = None,
    material_service: MaterialService | None = None,
    workspace_service: WorkspaceService | None = None,
    settings=None,
) -> FastAPI:
    settings = settings or load_settings()
    supplied_service = control_service

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active_account_service = account_service
        active_material_service = material_service
        active_workspace_service = workspace_service
        database = None
        if active_account_service is None and supplied_service is None:
            database = Database(settings.database)
            database.initialize()
            active_account_service = AccountService(
                database, settings.database, settings.auth
            )
            active_material_service = MaterialService(database, settings)
            active_workspace_service = WorkspaceService(database)
        if active_workspace_service is None and active_account_service is not None:
            active_workspace_service = WorkspaceService(active_account_service.database)
        app.state.account_service = active_account_service
        app.state.material_service = active_material_service
        app.state.workspace_service = active_workspace_service
        app.state.source_repository = CorpusSourceRepository(settings)
        if supplied_service is not None:
            app.state.control_service = supplied_service
            app.state.event_hub = EventHub()
            app.state.project_run = RunProjection(active_workspace_service, app.state.event_hub)
            supplied_service.scheduler.on_change = app.state.project_run
            await supplied_service.scheduler.recover()
            app.state.runtime_ready = True
            yield
            await supplied_service.scheduler.shutdown()
            if database is not None:
                database.close()
            return

        legacy_path = Path(settings.application.get("run_store_path", "backend/.runtime/runs.json"))
        run_store = SQLiteRunStore(legacy_path.with_suffix(".sqlite3"), legacy_path=legacy_path)
        events = EventHub(store=run_store)
        app.state.project_run = RunProjection(active_workspace_service, events)
        async with AsyncExitStack() as stack:
            stack.enter_context(run_store.owner())
            try:
                harnesses = await stack.enter_async_context(
                    open_harnesses(settings=settings, events=events, modes=("none", "low", "high"))
                )
                harness = harnesses[settings.model.thinking_mode]
                from backend.harness.lifecycle.executor import HarnessRunExecutor

                app.state.control_service = HarnessControlService(
                    HarnessRunExecutor(
                        harness, material_service=active_material_service,
                        history_service=active_workspace_service, harnesses=harnesses,
                    ),
                    store=run_store,
                )
                app.state.runtime_ready = True
            except PortError as exc:
                # Startup remains inspectable even when local model credentials,
                # the encoder, or the retrieval service are not configured.
                app.state.control_service = HarnessControlService(
                    UnavailableRunExecutor(exc.error)
                )
                app.state.runtime_ready = False
            app.state.event_hub = events
            app.state.control_service.scheduler.on_change = app.state.project_run
            await app.state.control_service.scheduler.recover()
            yield
            await app.state.control_service.scheduler.shutdown()
            if database is not None:
                database.close()

    app = FastAPI(
        title=settings.application["title"],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    # ASGI transports used by lightweight health probes do not necessarily
    # execute lifespan hooks. Keep the health route safe in that mode.
    app.state.runtime_ready = supplied_service is not None
    app.state.account_service = account_service
    app.state.material_service = material_service
    app.state.workspace_service = workspace_service
    app.state.source_repository = CorpusSourceRepository(settings)
    if supplied_service is not None:
        app.state.control_service = supplied_service
        app.state.event_hub = EventHub()

    def service(request: Request) -> HarnessControlService:
        return request.app.state.control_service

    def accounts(request: Request) -> AccountService | None:
        return request.app.state.account_service

    def workspace(request: Request) -> WorkspaceService | None:
        return request.app.state.workspace_service

    def source_repository(request: Request) -> CorpusSourceRepository:
        repository = getattr(request.app.state, "source_repository", None)
        if repository is None:
            repository = CorpusSourceRepository(settings)
            request.app.state.source_repository = repository
        return repository

    def bearer_token(authorization: str | None) -> str:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="authentication_required")
        token = authorization[7:].strip()
        if not token:
            raise HTTPException(status_code=401, detail="authentication_required")
        return token

    def current_account(
        request: Request, authorization: str | None
    ) -> AccountPrincipal | None:
        auth = accounts(request)
        if auth is None:
            # Injected control-plane tests deliberately run without identity.
            return None
        try:
            return auth.authenticate(bearer_token(authorization))
        except AccountError as exc:
            raise HTTPException(status_code=401, detail=exc.code) from None

    def require_account(
        request: Request, authorization: str | None
    ) -> AccountPrincipal:
        principal = current_account(request, authorization)
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication_required")
        return principal

    def require_conversation(
        request: Request,
        conversation_id: str,
        authorization: str | None,
        *,
        include_archived: bool = False,
    ) -> AccountPrincipal:
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        try:
            service_instance.get(
                account_id=principal.id,
                conversation_id=conversation_id,
                include_archived=include_archived,
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        return principal

    def require_session_owner(
        request: Request, session_id: str, authorization: str | None
    ) -> AccountPrincipal | None:
        principal = current_account(request, authorization)
        if principal is not None and principal.id != session_id:
            service_instance = workspace(request)
            if service_instance is None:
                raise HTTPException(status_code=403, detail="session_access_denied")
            try:
                service_instance.get(
                    account_id=principal.id, conversation_id=session_id
                )
            except WorkspaceError:
                raise HTTPException(
                    status_code=403, detail="session_access_denied"
                ) from None
        return principal

    def account_scope(principal: AccountPrincipal | None, session_id: str) -> str:
        return principal.id if principal is not None else session_id

    def sync_run(
        request: Request, principal: AccountPrincipal | None, run: RunEnvelope
    ) -> None:
        if principal is not None and run.account_id == principal.id:
            projection = getattr(request.app.state, "project_run", None)
            if projection is None:
                projection = RunProjection(workspace(request), request.app.state.event_hub)
            projection(run)

    @app.get(settings.application["health_path"])
    async def health(request: Request):
        return {"status": "ok"}

    def auth_view(result) -> AuthSessionView:
        return AuthSessionView(
            account=AccountView(
                id=result.account.id,
                email=result.account.email,
                display_name=result.account.display_name,
            ),
            session={"token": result.token},
        )

    @app.post("/auth/register", response_model=AuthSessionView, status_code=201)
    async def register(body: AuthCredentialsRequest, request: Request):
        auth = accounts(request)
        if auth is None:
            raise HTTPException(status_code=503, detail="unavailable")
        try:
            return auth_view(
                auth.register(
                    email=body.email,
                    password=body.password,
                    display_name=body.display_name,
                )
            )
        except AccountError as exc:
            status = 409 if exc.code == "account_email_exists" else 400
            raise HTTPException(status_code=status, detail=exc.code) from None

    @app.post("/auth/login", response_model=AuthSessionView)
    async def login(body: AuthCredentialsRequest, request: Request):
        auth = accounts(request)
        if auth is None:
            raise HTTPException(status_code=503, detail="unavailable")
        try:
            return auth_view(auth.login(email=body.email, password=body.password))
        except AccountError as exc:
            status = 401 if exc.code == "authentication_failed" else 400
            raise HTTPException(status_code=status, detail=exc.code) from None

    @app.get("/auth/me", response_model=AccountView)
    async def me(request: Request, authorization: str | None = Header(default=None)):
        principal = current_account(request, authorization)
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication_required")
        return AccountView(
            id=principal.id, email=principal.email, display_name=principal.display_name
        )

    @app.post("/auth/logout", status_code=204)
    async def logout(
        request: Request, authorization: str | None = Header(default=None)
    ):
        auth = accounts(request)
        if auth is None:
            return None
        try:
            auth.logout(bearer_token(authorization))
        except AccountError as exc:
            raise HTTPException(status_code=401, detail=exc.code) from None
        return None

    def conversation_view(
        conversation, run_count: int = 0
    ) -> WorkspaceConversationView:
        return WorkspaceConversationView(
            conversation_id=conversation.id,
            project_id=getattr(conversation, "project_id", None),
            title=conversation.title,
            mode=getattr(conversation, "mode", "chat") or "chat",
            pinned=conversation.pinned,
            archived_at=conversation.archived_at,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            run_count=run_count,
        )

    def project_view(project) -> WorkspaceProjectView:
        return WorkspaceProjectView(
            project_id=project.id,
            name=project.name,
            conversation_count=len(getattr(project, "conversations", []) or []),
            created_at=project.created_at,
            updated_at=project.updated_at,
        )

    def material_view(material) -> WorkspaceMaterialView:
        return WorkspaceMaterialView(
            material_id=material.id,
            name=material.filename,
            media_type=material.media_type,
            size_bytes=material.size_bytes,
            status=material.status,
            checksum=material.checksum,
            created_at=material.created_at,
        )

    def workbench_run_view(run: RunEnvelope) -> WorkbenchRunView:
        payload = RunInput.model_validate(run.payload).model_dump(mode="json")
        # Very old internal Harness checkpoints had no conversation mode. The
        # authenticated Workbench exposes those runs as the safe default.
        payload["mode"] = payload.get("mode") or "chat"
        raw_sources = run.execution_state.get("source_refs", []) if isinstance(run.execution_state, dict) else []
        sources = []
        for item in raw_sources if isinstance(raw_sources, list) else []:
            try:
                sources.append(SourceReference.model_validate(item))
            except (TypeError, ValueError):
                continue
        raw_artifacts = run.execution_state.get("artifacts", []) if isinstance(run.execution_state, dict) else []
        artifacts = []
        for item in raw_artifacts if isinstance(raw_artifacts, list) else []:
            try:
                artifacts.append(ArtifactReference.model_validate(item))
            except (TypeError, ValueError):
                continue
        recovery = recovery_for_run(run)
        return WorkbenchRunView(
            run_id=run.run_id,
            conversation_id=run.conversation_id or run.session_id,
            status=run.status,
            revision=run.revision,
            payload=WorkbenchRunPayload.model_validate(payload),
            pending_request_ids=list(run.pending_request_ids),
            answers=dict(run.answers),
            output=run.output,
            partial_output=run.partial_output,
            error=(
                WorkbenchErrorView(message=recovery.message)
                if recovery.message
                else None
            ),
            recovery=recovery,
            retry_of_run_id=run.retry_of_run_id,
            completed_units=run.completed_units,
            total_units=run.total_units,
            current_unit=run.current_unit,
            sources=sources,
            artifacts=artifacts,
            created_at=run.created_at,
            started_at=run.started_at,
            first_event_at=run.first_event_at,
            first_delta_at=run.first_delta_at,
            completed_at=run.completed_at,
            last_sequence=run.last_sequence,
        )

    @app.get(
        "/workspace/conversations",
        response_model=WorkspaceConversationListResponse,
    )
    async def list_conversations(
        request: Request, authorization: str | None = Header(default=None)
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        rows = service_instance.list(account_id=principal.id)
        return WorkspaceConversationListResponse(
            conversations=[conversation_view(item, count) for item, count in rows]
        )

    @app.post(
        "/workspace/conversations",
        response_model=WorkspaceConversationResponse,
        status_code=201,
    )
    async def create_conversation(
        body: CreateConversationRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        try:
            conversation = service_instance.create(
                account_id=principal.id,
                title=body.title,
                project_id=body.project_id,
                mode=body.mode,
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        return WorkspaceConversationResponse(
            conversation=conversation_view(conversation)
        )

    @app.get("/workspace/projects", response_model=WorkspaceProjectListResponse)
    async def list_projects(
        request: Request, authorization: str | None = Header(default=None)
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        return WorkspaceProjectListResponse(
            projects=[
                project_view(project)
                for project in service_instance.list_projects(account_id=principal.id)
            ]
        )

    @app.post(
        "/workspace/projects", response_model=WorkspaceProjectView, status_code=201
    )
    async def create_project(
        body: CreateProjectRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        try:
            return project_view(
                service_instance.create_project(account_id=principal.id, name=body.name)
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=400, detail=exc.code) from None

    @app.patch("/workspace/projects/{project_id}", response_model=WorkspaceProjectView)
    async def update_project(
        project_id: str,
        body: UpdateProjectRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        try:
            return project_view(
                service_instance.update_project(
                    account_id=principal.id, project_id=project_id, name=body.name
                )
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None

    @app.delete("/workspace/projects/{project_id}", status_code=204)
    async def delete_project(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        try:
            service_instance.delete_project(
                account_id=principal.id, project_id=project_id
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        return None

    @app.put(
        "/workspace/conversations/{conversation_id}/project",
        response_model=WorkspaceConversationResponse,
    )
    async def move_conversation_project(
        conversation_id: str,
        body: MoveConversationRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        try:
            conversation = service_instance.move_project(
                account_id=principal.id,
                conversation_id=conversation_id,
                project_id=body.project_id,
            )
            return WorkspaceConversationResponse(
                conversation=conversation_view(conversation)
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None

    @app.patch(
        "/workspace/conversations/{conversation_id}",
        response_model=WorkspaceConversationResponse,
    )
    async def update_conversation(
        conversation_id: str,
        body: UpdateConversationRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        try:
            conversation = service_instance.update(
                account_id=principal.id,
                conversation_id=conversation_id,
                title=body.title,
                pinned=body.pinned,
                mode=body.mode,
            )
        except WorkspaceError as exc:
            status = 409 if exc.code == "conversation_mode_immutable" else 404
            raise HTTPException(status_code=status, detail=exc.code) from None
        return WorkspaceConversationResponse(
            conversation=conversation_view(conversation)
        )

    @app.delete("/workspace/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(
        conversation_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        try:
            service_instance.delete(
                account_id=principal.id, conversation_id=conversation_id
            )
        except WorkspaceError as exc:
            status = 409 if exc.code == "conversation_has_active_runs" else 404
            raise HTTPException(status_code=status, detail=exc.code) from None
        return None

    @app.post("/workspace/conversations/{conversation_id}/archive", status_code=204)
    async def archive_conversation(
        conversation_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        try:
            service_instance.archive(
                account_id=principal.id, conversation_id=conversation_id
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        return None

    @app.post(
        "/workspace/conversations/{conversation_id}/restore",
        response_model=WorkspaceConversationResponse,
    )
    async def restore_conversation(
        conversation_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        try:
            conversation = service_instance.restore(
                account_id=principal.id, conversation_id=conversation_id
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        return WorkspaceConversationResponse(
            conversation=conversation_view(conversation)
        )

    @app.get(
        "/workspace/conversations/{conversation_id}",
        response_model=WorkspaceConversationDetailResponse,
    )
    async def conversation_detail(
        conversation_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        try:
            conversation = service_instance.get(
                account_id=principal.id,
                conversation_id=conversation_id,
                include_archived=True,
            )
            runs = service_instance.list_runs(
                account_id=principal.id,
                conversation_id=conversation_id,
                include_archived=True,
            )
            materials = service_instance.list_conversation_materials(
                account_id=principal.id,
                conversation_id=conversation_id,
                include_archived=True,
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        messages = []
        owned_materials = {item.id: item for item in service_instance.list_materials(account_id=principal.id)}
        for run in runs:
            stored_payload = json.loads(run.payload_json or "{}")
            try:
                progress = json.loads(run.progress_json or "{}")
            except (TypeError, ValueError):
                progress = {}
            raw_sources = progress.get("source_refs", []) if isinstance(progress, dict) else []
            run_sources = []
            for item in raw_sources if isinstance(raw_sources, list) else []:
                try:
                    run_sources.append(SourceReference.model_validate(item))
                except (TypeError, ValueError):
                    continue
            raw_artifacts = progress.get("artifacts", []) if isinstance(progress, dict) else []
            run_artifacts = []
            for item in raw_artifacts if isinstance(raw_artifacts, list) else []:
                try:
                    run_artifacts.append(ArtifactReference.model_validate(item))
                except (TypeError, ValueError):
                    continue
            attached_ids = stored_payload.get("attached_material_ids", [])
            messages.append(
                WorkspaceMessageView(
                    message_id=f"{run.run_id}:user",
                    role="user",
                    content=run.question,
                    updates=[text for text in stored_payload.get("facts", {}).get("user_answers", {}).values() if isinstance(text, str)],
                    materials=[material_view(owned_materials[key]) for key in attached_ids if key in owned_materials],
                    created_at=run.created_at,
                    run_id=run.run_id,
                    status=run.status,
                    artifacts=run_artifacts,
                )
            )
            visible_answer = run.answer or run.partial_output
            recovery = recovery_for_state(
                status=run.status,
                error_code=run.error_code,
                partial_output=run.partial_output,
                retryable=False,
            )
            if visible_answer or run.status in {"failed", "interrupted", "stopped"}:
                messages.append(
                    WorkspaceMessageView(
                        message_id=f"{run.run_id}:assistant",
                        role="assistant",
                        content=visible_answer or "",
                        created_at=run.completed_at or run.created_at,
                        run_id=run.run_id,
                        status=run.status,
                        # Internal error codes remain in the diagnostic store;
                        # the conversation contract exposes only recovery.
                        error_code=None,
                        recovery=recovery,
                        sources=run_sources,
                        artifacts=run_artifacts,
                    )
                )
        return WorkspaceConversationDetailResponse(
            conversation=conversation_view(conversation, len(runs)),
            messages=messages,
            materials=[material_view(item) for item in materials],
        )

    @app.get(
        "/workspace/sources/{source_id}",
        response_model=WorkspaceSourceResponse,
    )
    async def source_document(
        source_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        require_account(request, authorization)
        document = source_repository(request).get(source_id)
        if document is None:
            raise HTTPException(status_code=404, detail="source_not_found")
        return WorkspaceSourceResponse.model_validate(document.model_dump(mode="json"))

    # Workbench is the only public Run surface for the current frontend. The
    # legacy Harness routes below remain compatibility adapters and continue to
    # use their internal session_id contract.
    @app.post(
        "/workspace/conversations/{conversation_id}/runs",
        response_model=WorkbenchRunCommandResponse,
        status_code=202,
    )
    async def workbench_submit_run(
        conversation_id: str,
        body: WorkbenchSubmitRunRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_conversation(request, conversation_id, authorization)
        service_instance = workspace(request)
        conversation_mode = "chat"
        if service_instance is not None:
            conversation_mode = service_instance.get(
                account_id=principal.id, conversation_id=conversation_id
            ).mode
        if body.conversation_id != conversation_id:
            raise HTTPException(status_code=400, detail="conversation_id_mismatch")
        if body.dispatch_mode in {"queue", "steer"} and not body.text.strip():
            raise HTTPException(status_code=422, detail="text_required")
        if body.dispatch_mode == "queue" and not body.client_request_id:
            raise HTTPException(status_code=422, detail="client_request_id_required")
        if body.dispatch_mode in {"steer", "resume"}:
            if not body.target_run_id:
                raise HTTPException(status_code=400, detail="target_run_id_required")
            try:
                if body.dispatch_mode == "resume":
                    if not body.request_id:
                        raise HTTPException(
                            status_code=400, detail="request_id_required"
                        )
                    _, run = await service(request).resume(
                        session_id=conversation_id,
                        run_id=body.target_run_id,
                        request_id=body.request_id,
                        answer=body.answer,
                    )
                else:
                    from backend.contracts.modules.loop import RunInput

                    steer_material_ids = list(body.material_ids)
                    if not steer_material_ids:
                        service_instance = workspace(request)
                        if service_instance is not None:
                            steer_material_ids = (
                                service_instance.list_conversation_material_ids(
                                    account_id=principal.id,
                                    conversation_id=conversation_id,
                                )
                            )
                    if steer_material_ids:
                        materials = request.app.state.material_service
                        if materials is None:
                            raise HTTPException(
                                status_code=503, detail="material_service_unavailable"
                            )
                        try:
                            materials.resolve(
                                account_id=principal.id,
                                session_id=conversation_id,
                                conversation_id=conversation_id,
                                material_ids=steer_material_ids,
                            )
                        except MaterialServiceError as exc:
                            raise HTTPException(
                                status_code=404, detail=exc.code
                            ) from None
                    _, run = await service(request).steer(
                        session_id=conversation_id,
                        run_id=body.target_run_id,
                        payload=RunInput(
                            text=body.text,
                            facts=body.facts,
                            constraints=body.constraints,
                            material_ids=steer_material_ids,
                        ),
                    )
            except SchedulerCommandError as exc:
                raise _command_error(exc) from exc
            sync_run(request, principal, run)
            return WorkbenchRunCommandResponse(run=workbench_run_view(run))
        materials = request.app.state.material_service
        material_ids = list(body.material_ids)
        if body.attached_material_ids:
            if materials is None:
                raise HTTPException(status_code=503, detail="material_service_unavailable")
            try:
                materials.resolve(account_id=principal.id, session_id=conversation_id, conversation_id=conversation_id, material_ids=body.attached_material_ids)
            except MaterialServiceError as exc:
                raise HTTPException(status_code=404, detail=exc.code) from None
        if not material_ids and service_instance is not None:
            material_ids = service_instance.list_conversation_material_ids(
                account_id=principal.id, conversation_id=conversation_id
            )
        if material_ids:
            if materials is None:
                raise HTTPException(
                    status_code=503, detail="material_service_unavailable"
                )
            try:
                materials.resolve(
                    account_id=principal.id,
                    session_id=conversation_id,
                    conversation_id=conversation_id,
                    material_ids=material_ids,
                )
            except MaterialServiceError as exc:
                raise HTTPException(status_code=404, detail=exc.code) from None
        from backend.contracts.modules.loop import RunInput

        try:
            _, run = await service(request).submit(
                session_id=conversation_id,
                conversation_id=conversation_id,
                account_id=principal.id,
                client_request_id=body.client_request_id,
                retry_of_run_id=body.retry_of_run_id,
                payload=RunInput(
                    text=body.text,
                    mode=conversation_mode,
                    thinking_mode=body.thinking_mode or settings.model.thinking_mode,
                    facts=body.facts,
                    constraints=body.constraints,
                    material_ids=material_ids,
                    attached_material_ids=body.attached_material_ids,
                ),
            )
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        sync_run(request, principal, run)
        return WorkbenchRunCommandResponse(run=workbench_run_view(run))

    @app.get(
        "/workspace/conversations/{conversation_id}/snapshot",
        response_model=WorkbenchSnapshotResponse,
    )
    async def workbench_snapshot(
        conversation_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_conversation(
            request, conversation_id, authorization, include_archived=True
        )
        try:
            snapshot = await service(request).session(session_id=conversation_id)
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        for run in snapshot.runs:
            sync_run(request, principal, run)
        return WorkbenchSnapshotResponse(
            conversation_id=conversation_id,
            status=snapshot.status,
            active_run_id=snapshot.active_run_id,
            queued_run_ids=snapshot.queued_run_ids,
            waiting_run_ids=snapshot.waiting_run_ids,
            runs=[workbench_run_view(run) for run in snapshot.runs],
        )

    @app.get("/workspace/runs/{run_id}", response_model=WorkbenchRunCommandResponse)
    async def workbench_run(
        run_id: str,
        request: Request,
        conversation_id: str = Query(min_length=1),
        authorization: str | None = Header(default=None),
    ):
        principal = require_conversation(
            request, conversation_id, authorization, include_archived=True
        )
        try:
            run = await service(request).run(session_id=conversation_id, run_id=run_id)
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        if run.account_id != principal.id:
            raise HTTPException(status_code=404, detail="run_not_found")
        sync_run(request, principal, run)
        return WorkbenchRunCommandResponse(run=workbench_run_view(run))

    @app.get("/workspace/runs/{run_id}/artifacts/{artifact_id}")
    async def workbench_artifact(
        run_id: str,
        artifact_id: str,
        request: Request,
        conversation_id: str = Query(min_length=1),
        authorization: str | None = Header(default=None),
    ):
        """Download a completed artifact after the conversation boundary check."""
        principal = require_conversation(
            request, conversation_id, authorization, include_archived=True
        )
        try:
            run = await service(request).run(session_id=conversation_id, run_id=run_id)
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        if run.account_id != principal.id or run.status != "completed":
            raise HTTPException(status_code=404, detail="artifact_not_found")
        raw_artifacts = run.execution_state.get("artifacts", []) if isinstance(run.execution_state, dict) else []
        selected = next(
            (item for item in raw_artifacts if isinstance(item, dict) and item.get("artifact_id") == artifact_id),
            None,
        )
        relative = selected.get("relative_path") if selected else None
        if not isinstance(relative, str) or not relative:
            raise HTTPException(status_code=404, detail="artifact_not_found")
        root = Path(settings.execution.workspace_root)
        root = (Path(__file__).resolve().parents[3] / root).resolve() if not root.is_absolute() else root.resolve()
        workspace_root = (root / principal.id / conversation_id).resolve()
        target = (workspace_root / relative).resolve()
        if workspace_root != target and workspace_root not in target.parents:
            raise HTTPException(status_code=404, detail="artifact_not_found")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="artifact_not_found")
        try:
            expected_size = int(selected.get("size_bytes", -1))
            if expected_size >= 0 and target.stat().st_size != expected_size:
                raise HTTPException(status_code=404, detail="artifact_not_found")
            expected_sha = str(selected.get("sha256") or "")
            if expected_sha:
                digest = hashlib.sha256()
                with target.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != expected_sha:
                    raise HTTPException(status_code=404, detail="artifact_not_found")
        except (OSError, ValueError):
            raise HTTPException(status_code=404, detail="artifact_not_found") from None
        return FileResponse(
            target,
            media_type=selected.get("media_type") or "application/octet-stream",
            filename=selected.get("name") or target.name,
        )

    @app.get(
        "/workspace/runs/{run_id}/input-requests/{request_id}",
        response_model=WorkbenchInputRequestResponse,
    )
    async def workbench_input_request(
        run_id: str,
        request_id: str,
        request: Request,
        conversation_id: str = Query(min_length=1),
        authorization: str | None = Header(default=None),
    ):
        require_conversation(request, conversation_id, authorization)
        try:
            item = await service(request).request(
                session_id=conversation_id, request_id=request_id
            )
            if item.run_id != run_id:
                raise SchedulerCommandError("input request not found")
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        return WorkbenchInputRequestResponse(
            request=WorkbenchInputRequestView(
                request_id=item.request_id,
                run_id=item.run_id,
                question=item.question,
                options=[option.model_dump(mode="json") for option in item.options],
                allow_free_text=item.allow_free_text,
                required=item.required,
                status=item.status,
                answer=item.answer,
            )
        )

    @app.post(
        "/workspace/runs/{run_id}/resume", response_model=WorkbenchRunCommandResponse
    )
    async def workbench_resume(
        run_id: str,
        body: WorkbenchResumeRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_conversation(request, body.conversation_id, authorization)
        payload = None
        if body.attached_material_ids:
            current = await service(request).run(session_id=body.conversation_id, run_id=run_id)
            original = RunInput.model_validate(current.payload)
            ids = list(dict.fromkeys(original.material_ids + body.attached_material_ids))
            try:
                request.app.state.material_service.resolve(account_id=principal.id, session_id=body.conversation_id, conversation_id=body.conversation_id, material_ids=ids)
            except MaterialServiceError as exc:
                raise HTTPException(status_code=404, detail=exc.code) from None
            payload = original.model_copy(update={"material_ids": ids, "attached_material_ids": list(dict.fromkeys(original.attached_material_ids + body.attached_material_ids))})
        try:
            _, run = await service(request).resume(
                session_id=body.conversation_id,
                run_id=run_id,
                request_id=body.request_id,
                answer=body.answer,
                payload=payload,
            )
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        sync_run(request, principal, run)
        return WorkbenchRunCommandResponse(run=workbench_run_view(run))

    @app.post("/workspace/runs/{run_id}/continue", response_model=WorkbenchRunCommandResponse)
    async def workbench_continue(run_id: str, body: WorkbenchRunControlRequest, request: Request, authorization: str | None = Header(default=None)):
        principal = require_conversation(request, body.conversation_id, authorization)
        try:
            _, run = await service(request).continue_run(session_id=body.conversation_id, run_id=run_id)
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        sync_run(request, principal, run)
        return WorkbenchRunCommandResponse(run=workbench_run_view(run))

    @app.post(
        "/workspace/runs/{run_id}/steer", response_model=WorkbenchRunCommandResponse
    )
    async def workbench_steer(
        run_id: str,
        body: WorkbenchSteerRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_conversation(request, body.conversation_id, authorization)
        ids = list(body.material_ids) or workspace(request).list_conversation_material_ids(account_id=principal.id, conversation_id=body.conversation_id)
        ids = list(dict.fromkeys(ids + body.attached_material_ids))
        if ids:
            materials = request.app.state.material_service
            if materials is None:
                raise HTTPException(
                    status_code=503, detail="material_service_unavailable"
                )
            try:
                materials.resolve(
                    account_id=principal.id,
                    session_id=body.conversation_id,
                    conversation_id=body.conversation_id,
                    material_ids=ids,
                )
            except MaterialServiceError as exc:
                raise HTTPException(status_code=404, detail=exc.code) from None
        from backend.contracts.modules.loop import RunInput

        try:
            _, run = await service(request).steer(
                session_id=body.conversation_id,
                run_id=run_id,
                payload=RunInput(
                    text=body.text,
                    facts=body.facts,
                    constraints=body.constraints,
                    material_ids=ids,
                    attached_material_ids=body.attached_material_ids,
                ),
            )
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        sync_run(request, principal, run)
        return WorkbenchRunCommandResponse(run=workbench_run_view(run))

    async def workbench_cancel(
        run_id: str,
        body: WorkbenchRunControlRequest,
        request: Request,
        *,
        stop: bool,
        authorization: str | None,
    ):
        principal = require_conversation(request, body.conversation_id, authorization)
        try:
            action = service(request).stop if stop else service(request).interrupt
            _, run = await action(session_id=body.conversation_id, run_id=run_id)
            hub = request.app.state.event_hub
            if hub is not None and not run.partial_output:
                partial = visible_text(hub.stream_events_since(run_id=run_id))
                if partial:
                    run = await service(request).set_partial(
                        session_id=body.conversation_id,
                        run_id=run_id,
                        partial_output=partial,
                    )
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        sync_run(request, principal, run)
        return WorkbenchRunCommandResponse(run=workbench_run_view(run))

    @app.post(
        "/workspace/runs/{run_id}/interrupt", response_model=WorkbenchRunCommandResponse
    )
    async def workbench_interrupt(
        run_id: str,
        body: WorkbenchRunControlRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        return await workbench_cancel(
            run_id, body, request, stop=False, authorization=authorization
        )

    @app.post(
        "/workspace/runs/{run_id}/stop", response_model=WorkbenchRunCommandResponse
    )
    async def workbench_stop(
        run_id: str,
        body: WorkbenchRunControlRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        return await workbench_cancel(
            run_id, body, request, stop=True, authorization=authorization
        )

    @app.get("/workspace/conversations/{conversation_id}/events")
    async def workbench_events(
        conversation_id: str,
        request: Request,
        run_id: str = Query(min_length=1),
        after_sequence: int = Query(default=0, ge=0),
        timeout_seconds: float = Query(default=120.0, gt=0, le=600),
        authorization: str | None = Header(default=None),
    ):
        principal = require_conversation(
            request, conversation_id, authorization, include_archived=True
        )

        async def stream() -> AsyncIterator[bytes]:
            started = monotonic()
            cursor = after_sequence
            last_status: str | None = None
            while monotonic() - started < timeout_seconds:
                if await request.is_disconnected():
                    return
                try:
                    run = await service(request).run(
                        session_id=conversation_id, run_id=run_id
                    )
                except SchedulerCommandError:
                    return
                if run.account_id != principal.id:
                    return
                sync_run(request, principal, run)
                hub = request.app.state.event_hub
                if hub is not None:
                    for event in hub.stream_events_since(
                        run_id=run_id, after_sequence=cursor
                    ):
                        run.last_sequence = event.sequence
                        run.first_event_at = run.first_event_at or event.emitted_at
                        if event.type == "delta":
                            run.first_delta_at = run.first_delta_at or event.emitted_at
                        payload = {
                            "run_id": event.run_id,
                            "sequence": event.sequence,
                            "type": event.type,
                            "text": event.text,
                            "stage": event.stage,
                            "label": event.label,
                            "error": (
                                {"message": public_error_message(event.error)}
                                if event.error
                                else None
                            ),
                            "run": workbench_run_view(event.run).model_dump(mode="json")
                            if event.run
                            else None,
                        }
                        yield _json_line(payload)
                        cursor = event.sequence
                # Lifecycle is a snapshot at the current stream cursor.  It
                # is emitted after buffered deltas so a terminal snapshot can
                # never make the UI regress from completed to receiving.
                if run.status != last_status:
                    last_status = run.status
                    yield _json_line(
                        {
                            "type": "lifecycle",
                            "run_id": run.run_id,
                            "sequence": cursor,
                            "run": workbench_run_view(run).model_dump(mode="json"),
                        }
                    )
                    sync_run(request, principal, run)
                if hub is not None and hub.stream_events_since(run_id=run_id, after_sequence=cursor):
                    continue
                if run.status in {
                    "completed",
                    "failed",
                    "stopped",
                    "interrupted",
                    "waiting_user",
                }:
                    return
                await asyncio.sleep(0.1)

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.get("/workspace/materials", response_model=WorkspaceMaterialListResponse)
    async def list_workspace_materials(
        request: Request, authorization: str | None = Header(default=None)
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        return WorkspaceMaterialListResponse(
            materials=[
                material_view(item)
                for item in service_instance.list_materials(account_id=principal.id)
            ]
        )

    @app.get(
        "/workspace/materials/{material_id}/passages",
        response_model=WorkspacePassageListResponse,
    )
    async def preview_workspace_material(
        material_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        cursor: str | None = Query(default=None, max_length=1024),
    ):
        principal = require_account(request, authorization)
        materials = request.app.state.material_service
        if materials is None:
            raise HTTPException(status_code=503, detail="material_service_unavailable")
        try:
            materials.set_status(
                account_id=principal.id, material_id=material_id, status="parsing"
            )
            inputs = materials.resolve(
                account_id=principal.id,
                session_id=principal.id,
                material_ids=[material_id],
            )
            parsed = await asyncio.to_thread(
                MaterialParserRegistry(settings.materials).parse, inputs[0]
            )
        except MaterialServiceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        except PortError as exc:
            try:
                materials.set_status(
                    account_id=principal.id,
                    material_id=material_id,
                    status=(
                        "unsupported"
                        if exc.error.code == "material_type_unsupported"
                        else "failed"
                    ),
                )
            except MaterialServiceError:
                pass
            raise HTTPException(status_code=422, detail=exc.error.code) from None
        else:
            final_status = (
                "failed"
                if parsed.manifest.status == "failed"
                else "ready"
                if parsed.manifest.status in {"available", "partial"}
                else "unsupported"
            )
            materials.set_status(
                account_id=principal.id, material_id=material_id, status=final_status
            )
        view = read_view(
            parsed,
            MaterialReadInput(material_id=material_id, cursor=cursor),
            limit=settings.materials.read_chars,
            overview_limit=settings.materials.overview_units,
        )
        if view.status == "invalid_cursor":
            raise HTTPException(
                status_code=409, detail="material_cursor_expired_or_invalid"
            )
        return WorkspacePassageListResponse(
            chunks=[
                WorkspacePassageView(
                    id=unit.unit_id,
                    ordinal=unit.order + 1,
                    locator=unit.locator.label or unit.locator.value,
                    heading_path=unit.metadata.get("heading_path"),
                    text=unit.text,
                )
                for unit in view.units
            ],
            next_cursor=view.next_cursor,
            truncated=view.truncated,
            parse_status=parsed.manifest.status,
            complete=parsed.manifest.complete,
            notices=view.notices,
        )

    @app.post(
        "/workspace/materials",
        response_model=MaterialUploadResponse,
        status_code=201,
    )
    async def upload_library_material(
        request: Request,
        file: UploadFile = File(...),
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        materials = request.app.state.material_service
        if materials is None:
            raise HTTPException(status_code=503, detail="material_service_unavailable")
        content = await file.read(settings.materials.max_file_bytes + 1)
        try:
            material = materials.upload(
                account_id=principal.id,
                session_id=principal.id,
                filename=file.filename or "",
                media_type=file.content_type,
                content=content,
            )
        except MaterialServiceError as exc:
            status = 413 if exc.code == "material_too_large" else 400
            raise HTTPException(status_code=status, detail=exc.code) from None
        finally:
            await file.close()
        return MaterialUploadResponse(material=material)

    @app.delete("/workspace/materials/{material_id}", status_code=204)
    async def delete_library_material(
        material_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        materials = request.app.state.material_service
        if materials is None:
            raise HTTPException(status_code=503, detail="material_service_unavailable")
        try:
            materials.delete_account_material(
                account_id=principal.id, material_id=material_id
            )
        except MaterialServiceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        return None

    @app.post(
        "/workspace/conversations/{conversation_id}/materials",
        response_model=MaterialUploadResponse,
        status_code=201,
    )
    async def upload_workspace_material(
        conversation_id: str,
        request: Request,
        file: UploadFile = File(...),
        authorization: str | None = Header(default=None),
    ):
        principal = require_conversation(request, conversation_id, authorization)
        materials = request.app.state.material_service
        service_instance = workspace(request)
        if materials is None or service_instance is None:
            raise HTTPException(status_code=503, detail="material_service_unavailable")
        content = await file.read(settings.materials.max_file_bytes + 1)
        try:
            material = materials.upload(
                account_id=principal.id,
                # Material ownership is account-scoped; conversation access
                # is represented only by ConversationMaterialModel.
                session_id=principal.id,
                filename=file.filename or "",
                media_type=file.content_type,
                content=content,
            )
            service_instance.bind_material(
                account_id=principal.id,
                conversation_id=conversation_id,
                material_id=material.material_id,
            )
        except MaterialServiceError as exc:
            status = 413 if exc.code == "material_too_large" else 400
            raise HTTPException(status_code=status, detail=exc.code) from None
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        finally:
            await file.close()
        return MaterialUploadResponse(material=material)

    @app.delete(
        "/workspace/conversations/{conversation_id}/materials/{material_id}",
        status_code=204,
    )
    async def unbind_workspace_material(
        conversation_id: str,
        material_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        try:
            service_instance.unbind_material(
                account_id=principal.id,
                conversation_id=conversation_id,
                material_id=material_id,
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        return None

    @app.put(
        "/workspace/conversations/{conversation_id}/materials/{material_id}",
        status_code=204,
    )
    async def bind_workspace_material(
        conversation_id: str,
        material_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        try:
            service_instance.bind_material(
                account_id=principal.id,
                conversation_id=conversation_id,
                material_id=material_id,
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        return None

    @app.get(
        "/workspace/conversations/{conversation_id}/materials",
        response_model=WorkspaceMaterialListResponse,
    )
    async def list_conversation_materials(
        conversation_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        try:
            materials = service_instance.list_conversation_materials(
                account_id=principal.id, conversation_id=conversation_id
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        return WorkspaceMaterialListResponse(
            materials=[material_view(item) for item in materials]
        )

    @app.post(
        "/harness/sessions/{session_id}/materials",
        response_model=MaterialUploadResponse,
        status_code=201,
    )
    async def upload_material(
        session_id: str,
        request: Request,
        file: UploadFile = File(...),
        authorization: str | None = Header(default=None),
    ):
        principal = require_session_owner(request, session_id, authorization)
        materials = request.app.state.material_service
        if materials is None:
            raise HTTPException(status_code=503, detail="material_service_unavailable")
        content = await file.read(settings.materials.max_file_bytes + 1)
        try:
            material = materials.upload(
                account_id=account_scope(principal, session_id),
                session_id=session_id,
                filename=file.filename or "",
                media_type=file.content_type,
                content=content,
            )
        except MaterialServiceError as exc:
            status = 413 if exc.code == "material_too_large" else 400
            raise HTTPException(status_code=status, detail=exc.code) from None
        finally:
            await file.close()
        return MaterialUploadResponse(material=material)

    @app.delete(
        "/harness/sessions/{session_id}/materials/{material_id}", status_code=204
    )
    async def delete_material(
        session_id: str,
        material_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_session_owner(request, session_id, authorization)
        materials = request.app.state.material_service
        if materials is None:
            raise HTTPException(status_code=503, detail="material_service_unavailable")
        try:
            materials.delete(
                account_id=account_scope(principal, session_id),
                session_id=session_id,
                material_id=material_id,
            )
            if principal is not None and workspace(request) is not None:
                try:
                    workspace(request).unbind_material(
                        account_id=principal.id,
                        conversation_id=session_id,
                        material_id=material_id,
                    )
                except WorkspaceError:
                    pass
        except MaterialServiceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        return None

    @app.get("/harness/sessions/{session_id}", response_model=SessionResponse)
    async def session_snapshot(session_id: str, request: Request):
        require_session_owner(request, session_id, request.headers.get("authorization"))
        try:
            snapshot = await service(request).session(session_id=session_id)
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        return SessionResponse(snapshot=snapshot)

    @app.post(
        "/harness/sessions/{session_id}/runs",
        response_model=RunCommandResponse,
        status_code=202,
    )
    async def submit_run(
        session_id: str,
        body: SubmitRunRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        from backend.contracts.modules.loop import RunInput

        try:
            principal = require_session_owner(request, session_id, authorization)
            materials = request.app.state.material_service
            if body.material_ids:
                if materials is None:
                    raise HTTPException(
                        status_code=503, detail="material_service_unavailable"
                    )
                try:
                    materials.resolve(
                        account_id=account_scope(principal, session_id),
                        session_id=session_id,
                        material_ids=body.material_ids,
                    )
                except MaterialServiceError as exc:
                    raise HTTPException(status_code=404, detail=exc.code) from None
            material_ids = list(body.material_ids)
            # Legacy Harness callers may still send a RunInput-shaped body,
            # but the request must not be able to widen permissions.  Only a
            # persisted Workspace Conversation can opt into work mode; an
            # unbound legacy session remains read-only chat.
            conversation_mode = "chat"
            workspace_service = workspace(request)
            if workspace_service is not None:
                try:
                    conversation_mode = workspace_service.get(
                        account_id=account_scope(principal, session_id),
                        conversation_id=session_id,
                    ).mode
                except WorkspaceError:
                    # Legacy Harness sessions may not have a persisted
                    # Conversation.  Their request-level default remains chat.
                    pass
            if not material_ids and workspace(request) is not None:
                try:
                    material_ids = workspace(request).list_conversation_material_ids(
                        account_id=account_scope(principal, session_id),
                        conversation_id=session_id,
                    )
                except WorkspaceError:
                    # Legacy control-plane callers may use account_id as their
                    # scheduler key without a persisted Conversation.
                    material_ids = []
            dispatch, run = await service(request).submit(
                session_id=session_id,
                account_id=account_scope(principal, session_id),
                payload=RunInput(
                    text=body.text,
                    mode=conversation_mode,
                    facts=body.facts,
                    constraints=body.constraints,
                    material_ids=material_ids,
                ),
                task_id=body.task_id,
            )
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        sync_run(request, principal, run)
        return RunCommandResponse(dispatch=dispatch, run=run)

    @app.get("/harness/runs/{run_id}", response_model=RunEnvelope)
    async def run_snapshot(
        run_id: str,
        request: Request,
        session_id: str = Query(min_length=1),
        authorization: str | None = Header(default=None),
    ):
        try:
            principal = require_session_owner(request, session_id, authorization)
            run = await service(request).run(session_id=session_id, run_id=run_id)
            sync_run(request, principal, run)
            return run
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc

    @app.get(
        "/harness/runs/{run_id}/input-requests/{request_id}",
        response_model=InputRequestResponse,
    )
    async def input_request(
        run_id: str,
        request_id: str,
        request: Request,
        session_id: str = Query(min_length=1),
        authorization: str | None = Header(default=None),
    ):
        try:
            require_session_owner(request, session_id, authorization)
            item = await service(request).request(
                session_id=session_id, request_id=request_id
            )
            if item.run_id != run_id:
                raise SchedulerCommandError("input request not found")
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        return InputRequestResponse(request=item)

    @app.post("/harness/runs/{run_id}/resume", response_model=RunCommandResponse)
    async def resume_run(
        run_id: str,
        body: ResumeRunRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        try:
            principal = require_session_owner(request, body.session_id, authorization)
            dispatch, run = await service(request).resume(
                session_id=body.session_id,
                run_id=run_id,
                request_id=body.request_id,
                answer=body.answer,
            )
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        sync_run(request, principal, run)
        return RunCommandResponse(dispatch=dispatch, run=run)

    @app.post("/harness/runs/{run_id}/steer", response_model=RunCommandResponse)
    async def steer_run(
        run_id: str,
        body: SteerRunRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        from backend.contracts.modules.loop import RunInput

        try:
            principal = require_session_owner(request, body.session_id, authorization)
            if body.material_ids:
                materials = request.app.state.material_service
                if materials is None:
                    raise HTTPException(
                        status_code=503, detail="material_service_unavailable"
                    )
                try:
                    materials.resolve(
                        account_id=account_scope(principal, body.session_id),
                        session_id=body.session_id,
                        material_ids=body.material_ids,
                    )
                except MaterialServiceError as exc:
                    raise HTTPException(status_code=404, detail=exc.code) from None
            dispatch, run = await service(request).steer(
                session_id=body.session_id,
                run_id=run_id,
                payload=RunInput(
                    text=body.text,
                    facts=body.facts,
                    constraints=body.constraints,
                    material_ids=body.material_ids,
                ),
            )
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        sync_run(request, principal, run)
        return RunCommandResponse(dispatch=dispatch, run=run)

    async def cancel_run(
        run_id: str,
        body: RunControlRequest,
        request: Request,
        *,
        stop: bool,
        authorization: str | None = None,
    ):
        try:
            principal = require_session_owner(request, body.session_id, authorization)
            action = service(request).stop if stop else service(request).interrupt
            dispatch, run = await action(session_id=body.session_id, run_id=run_id)
            # A cooperative stop can happen after visible deltas have already
            # reached the client. Preserve those deltas in durable workspace
            # history even when the executor has no terminal answer.
            if not run.partial_output:
                hub = request.app.state.event_hub
                if hub is not None:
                    partial = visible_text(event for event in hub.stream_events if event.run_id == run_id)
                    if partial:
                        run = await service(request).set_partial(
                            session_id=body.session_id,
                            run_id=run_id,
                            partial_output=partial,
                        )
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        sync_run(request, principal, run)
        return RunCommandResponse(dispatch=dispatch, run=run)

    @app.post("/harness/runs/{run_id}/interrupt", response_model=RunCommandResponse)
    async def interrupt_run(
        run_id: str,
        body: RunControlRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        return await cancel_run(
            run_id, body, request, stop=False, authorization=authorization
        )

    @app.post("/harness/runs/{run_id}/stop", response_model=RunCommandResponse)
    async def stop_run(
        run_id: str,
        body: RunControlRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        return await cancel_run(
            run_id, body, request, stop=True, authorization=authorization
        )

    @app.get("/harness/sessions/{session_id}/events")
    async def events_stream(
        session_id: str,
        request: Request,
        run_id: str | None = Query(default=None),
        after_sequence: int = Query(default=0, ge=0),
        timeout_seconds: float = Query(default=120.0, gt=0, le=600),
        authorization: str | None = Header(default=None),
    ):
        principal = require_session_owner(request, session_id, authorization)

        async def stream() -> AsyncIterator[bytes]:
            started = monotonic()
            sent_events = 0
            last_status: dict[str, str] = {}
            sent_lifecycle_runs: set[str] = set()
            last_stream_sequences: dict[str, int] = {}
            if run_id:
                last_stream_sequences[run_id] = after_sequence
            while monotonic() - started < timeout_seconds:
                if await request.is_disconnected():
                    return
                try:
                    snapshot = await service(request).session(session_id=session_id)
                except SchedulerCommandError:
                    return
                matching = [
                    run
                    for run in snapshot.runs
                    if run_id is None or run.run_id == run_id
                ]
                if run_id and not matching:
                    return
                for run in matching:
                    sync_run(request, principal, run)
                    if (
                        run.status != last_status.get(run.run_id)
                        or run.run_id not in sent_lifecycle_runs
                    ):
                        last_status[run.run_id] = run.status
                        sent_lifecycle_runs.add(run.run_id)
                        yield _json_line(
                            {"type": "lifecycle", "run": run.model_dump(mode="json")}
                        )
                hub = request.app.state.event_hub
                if hub is not None:
                    for event in hub.events[sent_events:]:
                        if run_id is None or event.run_id == run_id:
                            yield _json_line(
                                {
                                    "type": "runtime",
                                    "event": event.model_dump(
                                        mode="json", exclude={"diagnostics"}
                                    ),
                                }
                            )
                    sent_events = len(hub.events)
                    for event in hub.stream_events_since(run_id=run_id):
                        cursor = last_stream_sequences.get(event.run_id, 0)
                        if event.sequence > cursor:
                            yield _json_line(
                                {
                                    "type": "stream",
                                    "event": event.model_dump(mode="json"),
                                }
                            )
                            last_stream_sequences[event.run_id] = event.sequence
                if matching and all(
                    run.status
                    in {"completed", "failed", "interrupted", "stopped", "waiting_user"}
                    for run in matching
                ):
                    return
                await asyncio.sleep(0.1)

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    return app


app = create_app()
