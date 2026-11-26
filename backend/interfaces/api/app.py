"""FastAPI adapter for the Harness control plane."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import sqlite3
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from time import monotonic
from typing import AsyncIterator

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from backend.application.accounts.preferences import (
    AccountPreferenceError,
    AccountPreferenceService,
)
from backend.application.accounts.service import (
    AccountError,
    AccountPrincipal,
    AccountService,
)
from backend.application.admin import AdminService, AdminServiceError
from backend.application.admin_conversations import AdminConversationService
from backend.application.admin_operations import AdminOperationsService
from backend.application.maintenance import RuntimeMaintenanceService
from backend.application.maritime.service import MaritimeError
from backend.application.model_directory import ModelDirectoryAdminService
from backend.application.ops_settings import OpsSettingsService
from backend.application.system_metrics import (
    HttpLatencyTracker,
    SystemMetricsService,
    elastic_storage_status,
    resolve_elastic_storage_policy,
)
from backend.application.workspace.conversations import WorkspaceError, WorkspaceService
from backend.application.workspace.materials import (
    MaterialService,
    MaterialServiceError,
)
from backend.application.captcha import GLOBAL_CAPTCHA
from backend.application.workspace.run_projection import RunProjection
from backend.infrastructure.execution.purge import (
    purge_account_files,
    purge_conversation_files,
    purge_material_files,
)
from backend.bootstrap import open_harnesses
from backend.config.settings import load_settings
from backend.contracts.account_settings import (
    AccountSummaryResponse,
    ChangePasswordRequest,
    ProfileUpdateRequest,
    UserPreferencesUpdateRequest,
    UserPreferencesView,
    UserPromptCommandCreateRequest,
    UserPromptCommandsResponse,
    UserPromptCommandUpdateRequest,
    UserPromptCommandView,
)
from backend.contracts.admin import (
    AdminAccountsResponse,
    AdminAccountUpdateRequest,
    AdminAccountView,
    AdminAuditResponse,
    AdminAuditView,
    AdminConversationDetailResponse,
    AdminConversationListResponse,
    AdminConnectivityTestResponse,
    AdminEmailTestRequest,
    AdminEmailTestResponse,
    AdminModelCreateRequest,
    AdminModelDirectoryResponse,
    AdminModelInferResponse,
    AdminModelUpdateRequest,
    AdminModelView,
    AdminOpsSettingsUpdateRequest,
    AdminOpsSettingsView,
    AdminProviderBalanceResponse,
    AdminProviderCreateRequest,
    AdminProviderDiscoverResponse,
    AdminProviderTestRequest,
    AdminProviderUpdateRequest,
    AdminProviderView,
    AdminOverviewResponse,
    AdminOverviewView,
    AdminCapabilitiesResponse,
    AdminCapabilityUpdateRequest,
    AdminCapabilityView,
    AdminResourceHealthResponse,
    AdminRuntimeHealthResponse,
    AdminSkillsResponse,
    AdminSystemMetricsResponse,
)
from backend.contracts.events.runtime import RunEvent
from backend.contracts.events.streaming import StreamEvent, visible_text
from backend.contracts.harness.artifacts import ArtifactReference
from backend.contracts.harness.lifecycle import RunEnvelope
from backend.contracts.model_profiles import ModelCatalogView, ModelRunSnapshot
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
from backend.infrastructure.email.resend import send_verification_email
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.parsing.read_view import read_view
from backend.infrastructure.parsing.registry import MaterialParserRegistry
from backend.infrastructure.persistence.run_store import SQLiteRunStore
from backend.infrastructure.sources import CorpusSourceRepository
from backend.interfaces.api.contracts import (
    AccountView,
    AuthConfigResponse,
    AuthCredentialsRequest,
    AuthSessionView,
    CaptchaChallengeResponse,
    CaptchaVerifyRequest,
    CaptchaVerifyResponse,
    CreateConversationRequest,
    CreateProjectRequest,
    MaterialUploadResponse,
    MoveConversationRequest,
    ResetPasswordRequest,
    SendVerificationCodeRequest,
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


_TRUSTED_PROXY_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


def _is_trusted_proxy_peer(host: str) -> bool:
    if not host or host in {"testclient", "localhost"}:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in _TRUSTED_PROXY_NETWORKS)


def _valid_ip_header(raw: str) -> str:
    candidate = raw.strip()
    if not candidate:
        return ""
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return ""
    return candidate[:64]


def _client_ip(request: Request) -> str:
    """Resolve visitor IP across Cloudflare Tunnel, ECS Nginx, and local setups."""
    peer = (request.client.host if request.client else "").strip()
    if _is_trusted_proxy_peer(peer):
        cf_ip = _valid_ip_header(request.headers.get("cf-connecting-ip", ""))
        if cf_ip:
            return cf_ip
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip[:64]
        forwarded = request.headers.get("x-forwarded-for", "").strip()
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            if parts:
                return parts[-1][:64]
    return peer[:64]


class _AuthFailureGuard:
    """Sliding-window brute-force throttle for login, register, and password reset."""

    def __init__(self, *, max_failures: int = 12, window_seconds: int = 600):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}

    def _prune(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        recent = [ts for ts in self._failures.get(key, ()) if ts >= cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def check(self, key: str) -> None:
        if not key:
            return
        if len(self._prune(key, monotonic())) >= self.max_failures:
            raise HTTPException(status_code=429, detail="auth_rate_limited")

    def record_failure(self, key: str) -> None:
        if not key:
            return
        now = monotonic()
        recent = self._prune(key, now)
        recent.append(now)
        self._failures[key] = recent
        if len(self._failures) > 4096:
            oldest = next(iter(self._failures))
            self._failures.pop(oldest, None)

    def reset(self, key: str) -> None:
        if key:
            self._failures.pop(key, None)


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

    def progress_events(self, run_id):
        if self.store:
            return self.store.progress_events(run_id)
        return [event.model_dump(mode="json", include={"type", "emitted_at", "stage", "label"})
                for event in self.stream_events_since(run_id=run_id)
                if event.type in {"progress", "delta"}]


class _HttpLatencyAsgiMiddleware:
    """Pure ASGI latency tracker (never buffers SSE/NDJSON streaming responses)."""

    def __init__(self, app, tracker: HttpLatencyTracker):
        self.app = app
        self.tracker = tracker

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.endswith("/events") or path == "/healthz":
            await self.app(scope, receive, send)
            return
        started = monotonic()
        status_code = 500
        self.tracker.enter()

        async def send_wrapper(message):
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 200))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            self.tracker.leave()
            self.tracker.record((monotonic() - started) * 1000.0, status_code)


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


def ensure_bootstrap_admin(service: AccountService, email: str, password: str) -> None:
    """Create the deployment administrator, tolerating a deliberate demotion.

    An administrator may demote or disable the deployment account.  Starting is
    fine while another live administrator exists; with none, nobody could
    manage the service, so refuse to start.
    """

    try:
        service.ensure_admin(email=email, password=password)
    except AccountError as exc:
        if exc.code not in {"admin_email_conflict", "admin_account_inactive"}:
            raise
        if not service.has_active_admin():
            raise
        logging.getLogger(__name__).warning("bootstrap_admin_not_active code=%s", exc.code)


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
            database.initialize(require_explicit_migrations=True)
            active_account_service = AccountService(
                database, settings.database, settings.auth
            )
            admin_email = os.environ.get("ZHOUXU_ADMIN_EMAIL", "").strip()
            admin_password = os.environ.get("ZHOUXU_ADMIN_PASSWORD", "")
            if bool(admin_email) != bool(admin_password):
                raise RuntimeError("admin_credentials_incomplete")
            if admin_email:
                ensure_bootstrap_admin(active_account_service, admin_email, admin_password)
            active_material_service = MaterialService(database, settings)
            active_workspace_service = WorkspaceService(database)
        if active_workspace_service is None and active_account_service is not None:
            active_workspace_service = WorkspaceService(active_account_service.database)
        app.state.settings = settings
        app.state.account_service = active_account_service
        app.state.admin_service = (
            AdminService(active_account_service.database)
            if active_account_service is not None
            else None
        )
        app.state.admin_conversation_service = (
            AdminConversationService(active_account_service.database)
            if active_account_service is not None
            else None
        )
        app.state.admin_operations_service = (
            AdminOperationsService(active_account_service.database, settings)
            if active_account_service is not None
            else None
        )
        app.state.account_preference_service = (
            AccountPreferenceService(active_account_service.database)
            if active_account_service is not None
            else None
        )
        app.state.ops_settings_service = (
            OpsSettingsService(active_account_service.database)
            if active_account_service is not None
            else None
        )
        app.state.model_directory = None
        app.state.model_directory_admin_service = None
        if active_account_service is not None:
            from backend.infrastructure.model.sqlite_directory import SqliteModelDirectory

            app.state.model_directory = SqliteModelDirectory(
                active_account_service.database, settings
            )
            app.state.model_directory.ensure_seed()
            app.state.model_directory_admin_service = ModelDirectoryAdminService(
                active_account_service.database
            )
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

        run_store_path = Path(settings.application.get("run_store_path", "backend/.runtime/runs.sqlite3"))
        run_store = SQLiteRunStore(run_store_path)
        app.state.run_store = run_store
        from backend.infrastructure.telemetry import metrics as telemetry

        from backend.config.settings import BACKEND_ROOT

        metrics_dir = run_store_path.parent if run_store_path.is_absolute() else BACKEND_ROOT.parent / run_store_path.parent
        app.state.metrics = telemetry.configure(metrics_dir / "telemetry.sqlite3")

        from backend.application.admin_catalog import AdminCatalogService

        app.state.admin_catalog_service = AdminCatalogService.from_settings(settings)
        app.state.admin_catalog_service.warm()
        app.state.maintenance_service = (
            RuntimeMaintenanceService(
                database=active_account_service.database,
                settings=settings,
                database_path=app.state.admin_catalog_service.database_path,
                runtime_dir=app.state.admin_catalog_service.runtime_dir,
            )
            if active_account_service is not None
            else None
        )
        maintenance_stop = asyncio.Event()
        maintenance_task: asyncio.Task | None = None
        if app.state.maintenance_service is not None and app.state.ops_settings_service is not None:
            maintenance_task = asyncio.create_task(
                app.state.maintenance_service.background_loop(
                    maintenance_stop,
                    app.state.ops_settings_service,
                )
            )
        events = EventHub(store=run_store)
        app.state.project_run = RunProjection(active_workspace_service, events)
        async with AsyncExitStack() as stack:
            stack.enter_context(run_store.owner())
            try:
                harnesses = await stack.enter_async_context(
                    open_harnesses(
                        settings=settings,
                        events=events,
                        modes=("none", "low", "medium", "high", "xhigh", "max"),
                        model_directory=app.state.model_directory,
                        account_skill_loader=getattr(
                            app.state, "maritime_skill_loader", None
                        ),
                        skill_permission_checker=getattr(
                            app.state, "check_skill_permission", None
                        ),
                    )
                )
                harness = harnesses[settings.model.thinking_mode]
                app.state.harness = harness
                if settings.research_cards.enabled:
                    app.state.information_card_retriever = harness.modules["action"].tools[
                        "information_card_search"
                    ].retriever
                from backend.harness.graph.executor import (
                    GraphRunExecutor,
                    RoutedRunExecutor,
                )
                from backend.harness.graph.handlers import build_graph_handlers
                from backend.harness.lifecycle.executor import HarnessRunExecutor

                graph_handlers = build_graph_handlers(harnesses, default_mode=settings.model.thinking_mode)
                app.state.control_service = HarnessControlService(
                    RoutedRunExecutor(
                        HarnessRunExecutor(
                            harness, material_service=active_material_service,
                            history_service=active_workspace_service, harnesses=harnesses,
                        ),
                        GraphRunExecutor(
                            graph_handlers.handlers, events=events,
                            prepare=graph_handlers.prepare, close=graph_handlers.close,
                            collect_artifacts=graph_handlers.collect_artifacts,
                        ),
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
            maintenance_stop.set()
            if maintenance_task is not None:
                maintenance_task.cancel()
                try:
                    await maintenance_task
                except asyncio.CancelledError:
                    pass
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
    app.state.http_latency_tracker = HttpLatencyTracker()
    app.state.active_sse_streams = 0
    app.state.last_elastic_cleanup_ts = 0.0
    app.add_middleware(
        _HttpLatencyAsgiMiddleware,
        tracker=app.state.http_latency_tracker,
    )
    # ASGI transports used by lightweight health probes do not necessarily
    # execute lifespan hooks. Keep the health route safe in that mode.
    app.state.runtime_ready = supplied_service is not None
    app.state.settings = settings
    app.state.account_service = account_service
    app.state.admin_service = (
        AdminService(account_service.database) if account_service is not None else None
    )
    app.state.admin_conversation_service = (
        AdminConversationService(account_service.database)
        if account_service is not None
        else None
    )
    app.state.admin_operations_service = (
        AdminOperationsService(account_service.database, settings)
        if account_service is not None
        else None
    )
    app.state.account_preference_service = (
        AccountPreferenceService(account_service.database)
        if account_service is not None
        else None
    )
    app.state.ops_settings_service = (
        OpsSettingsService(account_service.database)
        if account_service is not None
        else None
    )
    app.state.maintenance_service = None
    app.state.model_directory = None
    app.state.model_directory_admin_service = None
    if account_service is not None:
        from backend.infrastructure.model.sqlite_directory import SqliteModelDirectory

        app.state.model_directory = SqliteModelDirectory(account_service.database, settings)
        app.state.model_directory.ensure_seed()
        app.state.model_directory_admin_service = ModelDirectoryAdminService(
            account_service.database
        )
    app.state.material_service = material_service
    app.state.workspace_service = workspace_service or (
        WorkspaceService(account_service.database) if account_service is not None else None
    )
    app.state.source_repository = CorpusSourceRepository(settings)
    if supplied_service is not None:
        app.state.control_service = supplied_service
        app.state.event_hub = EventHub()

    # Maritime office features extend the existing workbench application.  The
    # router owns only its domain endpoints and resolves the authenticated
    # account through the same account service already used by the workbench;
    # it does not create a second application or harness.
    from backend.interfaces.api.maritime import attach_maritime, office_for

    attach_maritime(app, settings)

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

    def resolve_run_facts(
        request: Request,
        principal: AccountPrincipal,
        facts: dict,
        conversation_mode: str,
    ) -> dict:
        """Resolve optional maritime context through the existing workbench.

        The browser may send a Skill or case identifier, but never a resolved
        body.  The maritime adapter re-reads the authorized records and strips
        client supplied context before the Run enters the Harness.
        """

        values = dict(facts or {})
        values.pop("user_memory", None)
        requested_mode = values.get("task_mode")
        if requested_mode is not None:
            if not isinstance(requested_mode, str) or requested_mode not in {
                "chat",
                "work",
            }:
                raise HTTPException(status_code=422, detail="invalid_task_mode")
            if requested_mode != conversation_mode:
                raise HTTPException(
                    status_code=409, detail="conversation_mode_locked"
                )
        values.setdefault("task_mode", conversation_mode)
        pref_service = getattr(request.app.state, "account_preference_service", None)
        if pref_service is not None:
            memory = pref_service.active_memory(principal.id)
            if memory:
                values["user_memory"] = memory
        resolver = getattr(request.app.state, "resolve_maritime_skill", None)
        if resolver is None:
            return values
        return resolver(request, principal.id, values)

    def bearer_token(authorization: str | None) -> str:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="authentication_required")
        token = authorization[7:].strip()
        if not token:
            raise HTTPException(status_code=401, detail="authentication_required")
        return token

    def current_account(
        request: Request, authorization: str | None, *, update_last_seen: bool = True
    ) -> AccountPrincipal | None:
        auth = accounts(request)
        if auth is None:
            # Injected control-plane tests deliberately run without identity.
            return None
        try:
            return auth.authenticate(
                bearer_token(authorization), update_last_seen=update_last_seen
            )
        except AccountError as exc:
            raise HTTPException(status_code=401, detail=exc.code) from None

    def require_account(
        request: Request, authorization: str | None
    ) -> AccountPrincipal:
        principal = current_account(request, authorization)
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication_required")
        return principal

    def require_admin(
        request: Request, authorization: str | None
    ) -> AccountPrincipal:
        principal = require_account(request, authorization)
        if principal.role != "admin":
            raise HTTPException(status_code=403, detail="admin_required")
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

    def sync_run(
        request: Request, principal: AccountPrincipal | None, run: RunEnvelope
    ) -> None:
        if principal is not None and run.account_id == principal.id:
            projection = getattr(request.app.state, "project_run", None)
            if projection is None:
                projection = RunProjection(workspace(request), request.app.state.event_hub)
            projection(run)

    def admin_service(request: Request) -> AdminService:
        service_instance = getattr(request.app.state, "admin_service", None)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="admin_service_unavailable")
        return service_instance

    def admin_conversation_service(request: Request) -> AdminConversationService:
        service_instance = getattr(
            request.app.state, "admin_conversation_service", None
        )
        if service_instance is None:
            raise HTTPException(
                status_code=503, detail="admin_service_unavailable"
            )
        return service_instance

    def admin_operations_service(request: Request) -> AdminOperationsService:
        service_instance = getattr(request.app.state, "admin_operations_service", None)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="admin_service_unavailable")
        return service_instance

    def ops_settings_service(request: Request) -> OpsSettingsService:
        service_instance = getattr(request.app.state, "ops_settings_service", None)
        if service_instance is None:
            account_svc = getattr(request.app.state, "account_service", None)
            if account_svc is None:
                raise HTTPException(status_code=503, detail="admin_service_unavailable")
            service_instance = OpsSettingsService(account_svc.database)
            request.app.state.ops_settings_service = service_instance
        return service_instance

    def maintenance_service(request: Request) -> RuntimeMaintenanceService:
        service_instance = getattr(request.app.state, "maintenance_service", None)
        if service_instance is None:
            account_svc = getattr(request.app.state, "account_service", None)
            if account_svc is None:
                raise HTTPException(status_code=503, detail="admin_service_unavailable")
            cat = catalog_service(request)
            service_instance = RuntimeMaintenanceService(
                database=account_svc.database,
                settings=settings,
                database_path=cat.database_path,
                runtime_dir=cat.runtime_dir,
            )
            request.app.state.maintenance_service = service_instance
        return service_instance

    def system_metrics_service(request: Request) -> SystemMetricsService:
        service_instance = getattr(request.app.state, "system_metrics_service", None)
        account_svc = getattr(request.app.state, "account_service", None)
        db = account_svc.database if account_svc is not None else None
        if service_instance is None:
            service_instance = SystemMetricsService(settings=settings, database=db)
            request.app.state.system_metrics_service = service_instance
        elif service_instance.database is None and db is not None:
            service_instance.database = db
        return service_instance

    def _host_storage_snapshot(request: Request) -> dict | None:
        maint = getattr(request.app.state, "maintenance_service", None)
        if maint is None and getattr(request.app.state, "account_service", None) is not None:
            try:
                maint = maintenance_service(request)
            except Exception:
                maint = None
        return maint.host_storage() if maint is not None else None

    def _prepare_upload_quota(request: Request, principal: AccountPrincipal) -> int | None:
        if getattr(request.app.state, "account_service", None) is None:
            return None
        base_quota_mb = ops_settings_service(request).load().material_quota_mb
        account_quota_mb = admin_operations_service(request).account_grant(
            principal.id
        ).get("material_quota_mb")
        host_storage = _host_storage_snapshot(request)
        if host_storage is not None and host_storage.get("status") == "warn":
            if monotonic() - getattr(request.app.state, "last_elastic_cleanup_ts", 0.0) >= 300.0:
                request.app.state.last_elastic_cleanup_ts = monotonic()
                try:
                    maintenance_service(request).run_cleanup(reason="elastic_warn")
                    host_storage = _host_storage_snapshot(request)
                except Exception:
                    pass
        policy = resolve_elastic_storage_policy(
            base_quota_mb=base_quota_mb,
            host_storage=host_storage,
            account_quota_mb=account_quota_mb,
        )
        if policy["uploads_paused"]:
            raise HTTPException(status_code=507, detail="storage_space_low")
        if principal.role == "admin" and account_quota_mb is None:
            return None
        return policy["quota_bytes"]

    def model_directory(request: Request):
        directory = getattr(request.app.state, "model_directory", None)
        if directory is not None:
            return directory
        account_service = getattr(request.app.state, "account_service", None)
        if account_service is None:
            raise HTTPException(status_code=503, detail="model_directory_unavailable")
        from backend.infrastructure.model.sqlite_directory import SqliteModelDirectory

        directory = SqliteModelDirectory(account_service.database, settings)
        directory.ensure_seed()
        request.app.state.model_directory = directory
        request.app.state.model_directory_admin_service = ModelDirectoryAdminService(
            account_service.database
        )
        return directory

    def model_directory_admin(request: Request) -> ModelDirectoryAdminService:
        model_directory(request)
        service_instance = getattr(request.app.state, "model_directory_admin_service", None)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="model_directory_unavailable")
        return service_instance

    def admin_error(exc: AdminServiceError) -> HTTPException:
        if exc.code in {
            "admin_required",
            "model_not_permitted",
            "skill_not_permitted",
            "skill_upload_forbidden",
        }:
            return HTTPException(status_code=403, detail=exc.code)
        if exc.code in {
            "account_not_found",
            "conversation_not_found",
            "provider_not_found",
            "profile_not_found",
            "artifact_not_found",
            "backup_not_found",
        }:
            return HTTPException(status_code=404, detail=exc.code)
        if exc.code in {
            "invalid_account_status",
            "invalid_account_role",
            "invalid_account_pagination",
            "account_change_required",
            "invalid_audit_limit",
            "invalid_audit_category",
            "invalid_audit_outcome",
            "invalid_audit_cursor",
            "invalid_conversation_mode",
            "invalid_conversation_status",
            "invalid_conversation_limit",
            "invalid_conversation_cursor",
            "invalid_task_status",
            "provider_id_invalid",
            "provider_name_required",
            "preset_type_invalid",
            "profile_id_invalid",
            "model_protocol_invalid",
            "model_auth_style_invalid",
            "credential_mode_invalid",
            "model_family_invalid",
            "thinking_mode_invalid",
            "thinking_modes_invalid",
            "endpoint_required",
            "endpoint_https_required",
            "endpoint_invalid",
            "endpoint_localhost_blocked",
            "endpoint_private_address_blocked",
            "model_update_field_invalid",
            "context_tokens_invalid",
            "max_output_tokens_invalid",
            "upstream_model_required",
            "model_label_required",
            "provider_revision_missing",
            "capability_model_policy_invalid",
            "capability_skill_policy_invalid",
            "capability_model_list_invalid",
            "capability_model_list_required",
            "capability_change_required",
            "invalid_task_limit",
            "invalid_task_cursor",
            "credential_ref_required",
            "credential_ref_invalid",
            "credential_ref_forbidden",
            "credential_encryption_unconfigured",
            "credential_master_key_invalid",
            "credential_missing",
            "credential_decrypt_failed",
            "api_key_required",
            "api_key_not_allowed_for_env_ref",
            "extra_headers_invalid",
            "model_extra_header_forbidden",
            "access_scope_invalid",
            "capabilities_invalid",
            "sort_order_invalid",
            "model_must_be_enabled_for_default",
            "provider_update_field_invalid",
            "email_invalid",
            "email_provider_unconfigured",
            "material_quota_invalid",
            "backup_keep_days_invalid",
        }:
            return HTTPException(status_code=422, detail=exc.code)
        if exc.code in {"provider_exists", "profile_exists", "model_connectivity_unverified"}:
            return HTTPException(status_code=409, detail=exc.code)
        return HTTPException(status_code=409, detail=exc.code)

    @app.get("/admin/capabilities", response_model=AdminCapabilitiesResponse)
    async def admin_capabilities(
        request: Request, authorization: str | None = Header(default=None)
    ):
        require_admin(request, authorization)
        return AdminCapabilitiesResponse(
            accounts=[
                AdminCapabilityView(**item)
                for item in admin_operations_service(request).list_capabilities()
            ]
        )

    @app.patch(
        "/admin/capabilities/{account_id}", response_model=AdminCapabilityView
    )
    async def admin_update_capability(
        account_id: str,
        body: AdminCapabilityUpdateRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            item = admin_operations_service(request).update_capability(
                actor_id=principal.id,
                account_id=account_id,
                **body.model_dump(exclude_unset=True),
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminCapabilityView(**item)

    @app.get("/admin/skills", response_model=AdminSkillsResponse)
    async def admin_skills(
        request: Request, authorization: str | None = Header(default=None)
    ):
        require_admin(request, authorization)
        ops = admin_operations_service(request)
        uploaded = office_for(request.app, settings).uploaded_skills()
        emails = ops.account_emails(item["owner_id"] for item in uploaded)
        return AdminSkillsResponse(
            **ops.list_skills(),
            uploaded=[{**item, "owner_email": emails.get(item["owner_id"])} for item in uploaded],
        )

    @app.delete("/admin/skills/{skill_id}")
    async def admin_remove_skill(
        skill_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            removed = office_for(request.app, settings).admin_remove_skill(skill_id)
        except MaritimeError as exc:
            raise HTTPException(status_code=exc.status, detail="skill_not_found") from None
        try:
            admin_operations_service(request).record_skill_removed(
                actor_id=principal.id,
                owner_id=removed["owner_id"],
                skill_id=skill_id,
                name=removed["name"],
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return {"status": "removed"}

    @app.get("/admin/resources/health", response_model=AdminResourceHealthResponse)
    async def admin_resource_health(
        request: Request, authorization: str | None = Header(default=None)
    ):
        require_admin(request, authorization)
        return AdminResourceHealthResponse(
            **admin_operations_service(request).resource_health()
        )

    @app.get("/admin/runtime/health", response_model=AdminRuntimeHealthResponse)
    async def admin_runtime_health(
        request: Request, authorization: str | None = Header(default=None)
    ):
        require_admin(request, authorization)
        result = admin_operations_service(request).runtime_health(
            runtime_ready=getattr(request.app.state, "runtime_ready", None),
            event_hub_present=getattr(request.app.state, "event_hub", None) is not None,
            model_directory_present=getattr(request.app.state, "model_directory", None)
            is not None,
        )
        return AdminRuntimeHealthResponse(**result)

    @app.get("/admin/system/metrics", response_model=AdminSystemMetricsResponse)
    def admin_system_metrics(
        request: Request, authorization: str | None = Header(default=None)
    ):
        require_admin(request, authorization)
        ctrl = getattr(request.app.state, "control_service", None)
        scheduler_stats = (
            ctrl.scheduler.runtime_stats()
            if ctrl is not None and hasattr(ctrl.scheduler, "runtime_stats")
            else None
        )
        tracker = getattr(request.app.state, "http_latency_tracker", None)
        http_metrics = tracker.snapshot() if tracker is not None else None
        host_storage = _host_storage_snapshot(request)
        elastic_storage = None
        if getattr(request.app.state, "account_service", None) is not None and host_storage is not None:
            base_quota_mb = ops_settings_service(request).load().material_quota_mb
            elastic_storage = elastic_storage_status(base_quota_mb, host_storage)
        payload = system_metrics_service(request).collect(
            scheduler_stats=scheduler_stats,
            active_sse_streams=getattr(request.app.state, "active_sse_streams", 0),
            http_metrics=http_metrics,
            host_storage=host_storage,
            elastic_storage=elastic_storage,
        )
        return AdminSystemMetricsResponse(**payload)

    def catalog_service(request: Request):
        service_instance = getattr(request.app.state, "admin_catalog_service", None)
        if service_instance is None:
            from backend.application.admin_catalog import AdminCatalogService

            service_instance = AdminCatalogService.from_settings(settings)
            request.app.state.admin_catalog_service = service_instance
        return service_instance

    @app.get("/admin/tools")
    def admin_tools(
        request: Request, authorization: str | None = Header(default=None)
    ):
        require_admin(request, authorization)
        harness = getattr(request.app.state, "harness", None)
        if harness is None:
            raise HTTPException(status_code=503, detail="admin_service_unavailable")
        health = dict(admin_operations_service(request).resource_health()["sources"])
        health["sandbox"] = (
            {"status": "ready", "reason": f"{settings.execution.provider} 沙箱"}
            if getattr(harness, "execution_tools", None) is not None
            else {"status": "unavailable", "reason": "没有接入沙箱"}
        )
        return catalog_service(request).tools(harness, getattr(request.app.state, "metrics", None), health)

    @app.get("/admin/tools/{tool_name}/calls")
    def admin_tool_calls(
        tool_name: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        require_admin(request, authorization)
        metrics = getattr(request.app.state, "metrics", None)
        calls = metrics.recent_calls(tool_name, limit=20) if metrics else []
        emails = admin_operations_service(request).account_emails(item["account_id"] for item in calls)
        return {
            "calls": [{**item, "account_email": emails.get(item["account_id"])} for item in calls]
        }

    @app.get("/admin/data")
    def admin_data(
        request: Request, authorization: str | None = Header(default=None)
    ):
        require_admin(request, authorization)
        payload = catalog_service(request).data(
            admin_operations_service(request).resource_health(),
            getattr(request.app.state, "metrics", None),
        )
        maint = getattr(request.app.state, "maintenance_service", None)
        if maint is None and getattr(request.app.state, "account_service", None) is not None:
            maint = maintenance_service(request)
        if maint is not None:
            host_storage = maint.host_storage()
            payload["host_storage"] = host_storage
            if getattr(request.app.state, "account_service", None) is not None:
                base_quota_mb = ops_settings_service(request).load().material_quota_mb
                payload["elastic_storage"] = elastic_storage_status(base_quota_mb, host_storage)
            payload["cleanup_preview"] = maint.cleanup_preview()
            payload["backups"] = maint.list_backups()
        return payload

    @app.post("/admin/data/cleanup")
    def admin_data_cleanup(
        request: Request, authorization: str | None = Header(default=None)
    ):
        principal = require_admin(request, authorization)
        return maintenance_service(request).run_cleanup(
            actor_id=principal.id, reason="manual"
        )

    @app.post("/admin/data/backups")
    def admin_data_create_backup(
        request: Request, authorization: str | None = Header(default=None)
    ):
        principal = require_admin(request, authorization)
        cfg = ops_settings_service(request).load()
        return maintenance_service(request).create_backup(
            actor_id=principal.id,
            reason="manual",
            keep_count=cfg.backup_keep_days,
        )

    @app.get("/admin/data/backups/{filename}/download")
    def admin_data_download_backup(
        filename: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        target = maintenance_service(request).resolve_backup_path(filename)
        if target is None:
            raise HTTPException(status_code=404, detail="backup_not_found")
        account_svc = getattr(request.app.state, "account_service", None)
        if account_svc is not None:
            with account_svc.database.session() as session:
                AdminService._add_audit(
                    session,
                    actor_id=principal.id,
                    target_account_id=None,
                    action="system.backup_downloaded",
                    resource_type="system",
                    resource_id=filename,
                    details={"size_bytes": target.stat().st_size},
                )
                session.commit()
        return FileResponse(
            target,
            media_type="application/zip",
            filename=target.name,
        )

    @app.get("/admin/ops/settings", response_model=AdminOpsSettingsView)
    def admin_get_ops_settings(
        request: Request, authorization: str | None = Header(default=None)
    ):
        principal = require_admin(request, authorization)
        try:
            return AdminOpsSettingsView(
                **ops_settings_service(request).admin_view(actor_id=principal.id)
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None

    @app.patch("/admin/ops/settings", response_model=AdminOpsSettingsView)
    def admin_update_ops_settings(
        body: AdminOpsSettingsUpdateRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            return AdminOpsSettingsView(
                **ops_settings_service(request).update(
                    actor_id=principal.id,
                    data=body.model_dump(mode="json", exclude_unset=True),
                )
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None

    @app.post("/admin/ops/email-test", response_model=AdminEmailTestResponse)
    async def admin_test_ops_email(
        body: AdminEmailTestRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            res = await ops_settings_service(request).send_test_email(
                actor_id=principal.id, to_email=body.to_email
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminEmailTestResponse(
            ok=res.ok,
            message_id=res.message_id,
            error_code=res.error_code,
            detail=res.detail,
        )

    @app.get("/admin/overview", response_model=AdminOverviewResponse)
    async def admin_overview(
        request: Request, authorization: str | None = Header(default=None)
    ):
        require_admin(request, authorization)
        try:
            attention = admin_operations_service(request).attention_summary()
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminOverviewResponse(
            overview=AdminOverviewView(**admin_service(request).overview(), **attention)
        )

    @app.get("/admin/accounts", response_model=AdminAccountsResponse)
    async def admin_accounts(
        request: Request,
        authorization: str | None = Header(default=None),
        q: str = Query(default="", max_length=320),
        status: str | None = Query(default=None),
        role: str | None = Query(default=None),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ):
        require_admin(request, authorization)
        try:
            result = admin_service(request).list_accounts_page(
                query=q, status=status, role=role, page=page, page_size=page_size
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminAccountsResponse(**result)

    @app.patch(
        "/admin/accounts/{account_id}", response_model=AdminAccountView
    )
    async def admin_update_account(
        account_id: str,
        body: AdminAccountUpdateRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            updated = admin_service(request).update_account(
                actor_id=principal.id,
                account_id=account_id,
                status=body.status,
                role=body.role,
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminAccountView(**updated)

    @app.delete("/admin/accounts/{account_id}")
    async def admin_delete_account(
        account_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            deleted = admin_service(request).delete_account(
                actor_id=principal.id,
                account_id=account_id,
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        control = getattr(request.app.state, "control_service", None)
        hub = getattr(request.app.state, "event_hub", None)
        if control is not None:
            for conversation_id in deleted["conversation_ids"]:
                try:
                    run_ids = await control.discard_session(session_id=conversation_id)
                except SchedulerCommandError:
                    run_ids = []
                if hub is not None and run_ids:
                    hub.discard_runs(run_ids)
        purge_account_files(settings, account_id)
        purge_material_files(settings, deleted["material_storage_keys"])
        skills_deleted = 0
        try:
            skills_deleted = office_for(request.app, settings).admin_remove_account_skills(account_id)
        except Exception:
            skills_deleted = 0
        return {
            "deleted": True,
            "account_id": deleted["account_id"],
            "email": deleted["email"],
            "conversations_deleted": len(deleted["conversation_ids"]),
            "materials_deleted": len(deleted["material_storage_keys"]),
            "skills_deleted": skills_deleted,
        }

    @app.get("/admin/audit/summary")
    async def admin_audit_summary(
        request: Request, authorization: str | None = Header(default=None)
    ):
        require_admin(request, authorization)
        return admin_service(request).audit_summary()

    @app.get("/admin/audit", response_model=AdminAuditResponse)
    async def admin_audit(
        request: Request,
        authorization: str | None = Header(default=None),
        category: str | None = Query(default=None),
        outcome: str | None = Query(default=None),
        q: str = Query(default="", max_length=200),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        require_admin(request, authorization)
        try:
            page = admin_service(request).query_audit(
                category=category,
                outcome=outcome,
                query=q,
                cursor=cursor,
                limit=limit,
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminAuditResponse(
            events=[AdminAuditView(**item) for item in page["events"]],
            category_counts=page["category_counts"],
            next_cursor=page["next_cursor"],
        )

    @app.post(
        "/admin/model-providers",
        response_model=AdminProviderView,
        status_code=201,
    )
    async def admin_create_model_provider(
        body: AdminProviderCreateRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            result = model_directory_admin(request).create_provider(
                actor_id=principal.id, data=body.model_dump(mode="json")
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminProviderView(**result)

    @app.get(
        "/admin/model-directory",
        response_model=AdminModelDirectoryResponse,
    )
    async def admin_model_directory(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            result = model_directory_admin(request).list_directory(actor_id=principal.id)
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminModelDirectoryResponse(**result)

    @app.patch(
        "/admin/model-providers/{provider_id}",
        response_model=AdminProviderView,
    )
    async def admin_update_model_provider(
        provider_id: str,
        body: AdminProviderUpdateRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            result = model_directory_admin(request).update_provider(
                actor_id=principal.id,
                provider_id=provider_id,
                data=body.model_dump(mode="json", exclude_unset=True),
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminProviderView(**result)

    @app.post(
        "/admin/models",
        response_model=AdminModelView,
        status_code=201,
    )
    async def admin_create_model(
        body: AdminModelCreateRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            result = model_directory_admin(request).create_profile(
                actor_id=principal.id, data=body.model_dump(mode="json")
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminModelView(**result)

    @app.patch(
        "/admin/models/{profile_id}",
        response_model=AdminModelView,
    )
    async def admin_update_model(
        profile_id: str,
        body: AdminModelUpdateRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            result = model_directory_admin(request).update_profile(
                actor_id=principal.id,
                profile_id=profile_id,
                data=body.model_dump(mode="json", exclude_unset=True),
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminModelView(**result)

    @app.delete("/admin/model-providers/{provider_id}")
    async def admin_archive_model_provider(
        provider_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            return model_directory_admin(request).archive_provider(
                actor_id=principal.id, provider_id=provider_id
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None

    @app.delete("/admin/models/{profile_id}")
    async def admin_archive_model(
        profile_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            return model_directory_admin(request).archive_profile(
                actor_id=principal.id, profile_id=profile_id
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None

    @app.post(
        "/admin/model-providers/{provider_id}/discover",
        response_model=AdminProviderDiscoverResponse,
    )
    async def admin_discover_provider_models(
        provider_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            result = await model_directory_admin(request).discover_models(
                actor_id=principal.id, provider_id=provider_id
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminProviderDiscoverResponse(**result)

    @app.get(
        "/admin/model-providers/{provider_id}/balance",
        response_model=AdminProviderBalanceResponse,
    )
    async def admin_provider_balance(
        provider_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            result = await model_directory_admin(request).query_provider_balance(
                actor_id=principal.id, provider_id=provider_id
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminProviderBalanceResponse(**result)

    @app.get(
        "/admin/models/infer",
        response_model=AdminModelInferResponse,
    )
    async def admin_infer_model_defaults(
        request: Request,
        model_id: str = Query(..., min_length=1, max_length=200),
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            result = model_directory_admin(request).infer_model(
                actor_id=principal.id, model_id=model_id
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminModelInferResponse(**result)

    @app.post(
        "/admin/model-providers/{provider_id}/test",
        response_model=AdminConnectivityTestResponse,
    )
    async def admin_test_model_provider(
        provider_id: str,
        request: Request,
        body: AdminProviderTestRequest | None = None,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            result = await model_directory_admin(request).test_provider(
                actor_id=principal.id,
                provider_id=provider_id,
                model_id=body.model_id if body is not None else None,
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminConnectivityTestResponse(**result)

    @app.post(
        "/admin/models/{profile_id}/test",
        response_model=AdminConnectivityTestResponse,
    )
    async def admin_test_model(
        profile_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            result = await model_directory_admin(request).test_profile(
                actor_id=principal.id, profile_id=profile_id
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminConnectivityTestResponse(**result)

    @app.get(
        "/admin/accounts/{account_id}/conversations",
        response_model=AdminConversationListResponse,
    )
    async def admin_account_conversations(
        account_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        query: str = Query(default="", max_length=200),
        mode: str | None = Query(default=None),
        archived: bool | None = Query(default=None),
        status: str | None = Query(default=None),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        require_admin(request, authorization)
        try:
            result = admin_conversation_service(request).list_conversations(
                account_id=account_id,
                query=query,
                mode=mode,
                archived=archived,
                status=status,
                cursor=cursor,
                limit=limit,
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminConversationListResponse(**result)

    @app.get(
        "/admin/accounts/{account_id}/conversations/{conversation_id}",
        response_model=AdminConversationDetailResponse,
    )
    async def admin_account_conversation_detail(
        account_id: str,
        conversation_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        request_id: str | None = Header(default=None, alias="X-Request-ID"),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100),
    ):
        principal = require_admin(request, authorization)
        try:
            result = admin_conversation_service(request).conversation_detail(
                actor_id=principal.id,
                account_id=account_id,
                conversation_id=conversation_id,
                cursor=cursor,
                limit=limit,
                request_id=request_id,
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        return AdminConversationDetailResponse(**result)

    @app.get(
        "/admin/sources/{source_id}",
        response_model=WorkspaceSourceResponse,
    )
    async def admin_source_document(
        source_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        require_admin(request, authorization)
        try:
            document = await asyncio.to_thread(source_repository(request).get, source_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except OSError:
            raise HTTPException(status_code=503, detail="paper_original_unavailable") from None
        if document is None:
            raise HTTPException(status_code=404, detail="source_not_found")
        return WorkspaceSourceResponse.model_validate(document.model_dump(mode="json"))

    @app.get(
        "/admin/materials/{material_id}/passages",
        response_model=WorkspacePassageListResponse,
    )
    async def admin_preview_material(
        material_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        cursor: str | None = Query(default=None, max_length=1024),
    ):
        principal = require_admin(request, authorization)
        materials = request.app.state.material_service
        if materials is None:
            raise HTTPException(status_code=503, detail="material_service_unavailable")
        try:
            record, mat_input = materials.resolve_for_admin(material_id=material_id)
            parsed = await asyncio.to_thread(
                MaterialParserRegistry(settings.materials).parse, mat_input
            )
        except MaterialServiceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        except PortError as exc:
            raise HTTPException(status_code=422, detail=exc.error.code) from None
        view = read_view(
            parsed,
            MaterialReadInput(material_id=material_id, cursor=cursor),
            limit=settings.materials.read_chars,
            overview_limit=settings.materials.overview_units,
        )
        if view.status == "invalid_cursor":
            raise HTTPException(status_code=409, detail="material_cursor_expired_or_invalid")
        if cursor is None:
            with materials.database.session() as session:
                AdminService._add_audit(
                    session,
                    actor_id=principal.id,
                    target_account_id=record.account_id,
                    action="material.viewed",
                    resource_type="material",
                    resource_id=record.id,
                    details={"filename": record.filename},
                )
                session.commit()
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

    @app.get("/admin/materials/{material_id}/download")
    async def admin_download_material(
        material_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        materials = request.app.state.material_service
        if materials is None:
            raise HTTPException(status_code=503, detail="material_service_unavailable")
        try:
            record, mat_input = materials.resolve_for_admin(material_id=material_id)
        except MaterialServiceError as exc:
            raise HTTPException(status_code=404, detail=exc.code) from None
        with materials.database.session() as session:
            AdminService._add_audit(
                session,
                actor_id=principal.id,
                target_account_id=record.account_id,
                action="material.downloaded",
                resource_type="material",
                resource_id=record.id,
                details={"filename": record.filename, "size_bytes": record.size_bytes},
            )
            session.commit()
        return FileResponse(
            Path(mat_input.uri),
            media_type=record.media_type or "application/octet-stream",
            filename=record.filename,
        )

    @app.get("/admin/accounts/{account_id}/conversations/{conversation_id}/runs/{run_id}/artifacts/{artifact_id}")
    async def admin_download_run_artifact(
        account_id: str,
        conversation_id: str,
        run_id: str,
        artifact_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_admin(request, authorization)
        try:
            selected = admin_conversation_service(request).resolve_run_artifact(
                account_id=account_id,
                conversation_id=conversation_id,
                run_id=run_id,
                artifact_id=artifact_id,
            )
        except AdminServiceError as exc:
            raise admin_error(exc) from None
        root = Path(settings.execution.workspace_root)
        root = (Path(__file__).resolve().parents[3] / root).resolve() if not root.is_absolute() else root.resolve()
        workspace_root = (root / account_id / conversation_id).resolve()
        target = (workspace_root / selected["relative_path"]).resolve()
        if workspace_root != target and workspace_root not in target.parents or not target.is_file():
            raise HTTPException(status_code=404, detail="artifact_not_found")
        expected_sha = str(selected.get("sha256") or "")
        if expected_sha:
            digest = hashlib.sha256()
            with target.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected_sha:
                raise HTTPException(status_code=404, detail="artifact_not_found")
        account_svc = getattr(request.app.state, "account_service", None)
        if account_svc is not None:
            with account_svc.database.session() as session:
                AdminService._add_audit(
                    session,
                    actor_id=principal.id,
                    target_account_id=account_id,
                    action="artifact.downloaded",
                    resource_type="artifact",
                    resource_id=artifact_id,
                    details={"name": selected.get("name"), "run_id": run_id},
                )
                session.commit()
        return FileResponse(
            target,
            media_type=selected.get("media_type") or "application/octet-stream",
            filename=selected.get("name") or target.name,
        )

    @app.get(settings.application["health_path"])
    async def health(request: Request):
        store = getattr(request.app.state, "run_store", None)
        if store is not None:
            try:
                store.visible("__health_probe__")
            except sqlite3.Error as exc:
                raise HTTPException(status_code=503, detail="runtime_storage_unavailable") from exc
        return {"status": "ok"}

    @app.get("/workspace/models", response_model=ModelCatalogView)
    async def model_catalog(
        request: Request, authorization: str | None = Header(default=None)
    ):
        """Return grouped, secret-free profiles permitted to the authenticated account."""
        principal = require_account(request, authorization)
        from backend.infrastructure.model.registry import ModelRegistry

        directory = model_directory(request)
        revisions = directory.list_current()
        ops = getattr(request.app.state, "admin_operations_service", None)
        if ops is not None:
            revisions = ops.filter_model_revisions(
                account_id=principal.id,
                account_role=principal.role,
                revisions=revisions,
            )
        return ModelRegistry(settings, directory=directory).catalog_view(
            profiles=[rev.profile for rev in revisions]
        )

    def auth_view(result) -> AuthSessionView:
        return AuthSessionView(
            account=AccountView(
                id=result.account.id,
                email=result.account.email,
                display_name=result.account.display_name,
                role=result.account.role,
            ),
            session={"token": result.token},
        )

    _AUTH_GUARD = _AuthFailureGuard()

    @app.get("/auth/config", response_model=AuthConfigResponse)
    async def auth_config(request: Request):
        auth = getattr(request.app.state, "account_service", None)
        if auth is None:
            return AuthConfigResponse()
        return AuthConfigResponse(**ops_settings_service(request).public_auth_config())

    @app.post("/auth/captcha/challenge", response_model=CaptchaChallengeResponse)
    async def auth_captcha_challenge(request: Request):
        client_ip = _client_ip(request)
        ch = GLOBAL_CAPTCHA.create_challenge(client_ip=client_ip)
        return CaptchaChallengeResponse(
            challenge_id=ch.challenge_id,
            bg_image=ch.bg_image,
            piece_image=ch.piece_image,
            y=ch.y,
            width=ch.width,
            height=ch.height,
            piece_width=ch.piece_width,
            piece_height=ch.piece_height,
        )

    @app.post("/auth/captcha/verify", response_model=CaptchaVerifyResponse)
    async def auth_captcha_verify(body: CaptchaVerifyRequest, request: Request):
        client_ip = _client_ip(request)
        ok, token, err = GLOBAL_CAPTCHA.verify_challenge(
            challenge_id=body.challenge_id,
            x=body.x,
            duration_ms=body.duration_ms,
            trail=body.trail,
            client_ip=client_ip,
        )
        return CaptchaVerifyResponse(verified=ok, captcha_token=token, error=err)

    @app.post("/auth/send-code")
    async def send_auth_code(body: SendVerificationCodeRequest, request: Request):
        auth = accounts(request)
        if auth is None:
            raise HTTPException(status_code=503, detail="unavailable")
        cfg = ops_settings_service(request).load()
        if not cfg.email_ready:
            raise HTTPException(status_code=503, detail="email_provider_unconfigured")
        if body.purpose == "register" and not cfg.email_verification_enabled:
            raise HTTPException(status_code=400, detail="email_verification_disabled")
        if body.purpose == "reset_password" and not cfg.allow_password_reset:
            raise HTTPException(status_code=403, detail="password_reset_disabled")

        client_ip = _client_ip(request)
        if cfg.captcha_enabled and not GLOBAL_CAPTCHA.validate_token(body.captcha_token or "", client_ip=client_ip):
            raise HTTPException(status_code=403, detail="captcha_required")

        try:
            code_id, code = auth.issue_verification_code(
                email=body.email,
                purpose=body.purpose,
                request_ip=client_ip,
            )
        except AccountError as exc:
            status = (
                409
                if exc.code == "account_email_exists"
                else 429
                if exc.code in {"verification_cooldown", "verification_rate_limited"}
                else 403
                if exc.code == "account_disabled"
                else 404
                if exc.code == "email_not_registered"
                else 400
            )
            raise HTTPException(status_code=status, detail=exc.code) from None

        res = await send_verification_email(
            api_key=cfg.resend_api_key,
            from_email=cfg.resend_from_email,
            from_name=cfg.resend_from_name,
            to_email=body.email.strip().lower(),
            purpose=body.purpose,
            code=code,
            expires_minutes=5,
        )
        if not res.ok:
            auth.cancel_verification_code(code_id)
            raise HTTPException(status_code=502, detail=res.error_code or "email_send_failed")
        return {"sent": True, "expires_in_seconds": 300, "cooldown_seconds": 60}

    @app.post("/auth/reset-password", response_model=AuthSessionView)
    async def reset_password(body: ResetPasswordRequest, request: Request):
        auth = accounts(request)
        if auth is None:
            raise HTTPException(status_code=503, detail="unavailable")
        cfg = ops_settings_service(request).load()
        if not cfg.allow_password_reset or not cfg.email_ready:
            raise HTTPException(status_code=403, detail="password_reset_disabled")
        guard_key = f"reset:{_client_ip(request)}"
        _AUTH_GUARD.check(guard_key)
        try:
            result = auth_view(
                auth.reset_password(
                    email=body.email,
                    verification_code=body.verification_code,
                    new_password=body.new_password,
                )
            )
            _AUTH_GUARD.reset(guard_key)
            return result
        except AccountError as exc:
            _AUTH_GUARD.record_failure(guard_key)
            status = (
                404
                if exc.code == "email_not_registered"
                else 403
                if exc.code == "account_disabled"
                else 400
            )
            raise HTTPException(status_code=status, detail=exc.code) from None

    @app.post("/auth/register", response_model=AuthSessionView, status_code=201)
    async def register(body: AuthCredentialsRequest, request: Request):
        auth = accounts(request)
        if auth is None:
            raise HTTPException(status_code=503, detail="unavailable")
        cfg = ops_settings_service(request).load()
        require_code = cfg.email_verification_enabled and cfg.email_ready
        client_ip = _client_ip(request)
        guard_key = f"register:{client_ip}"
        _AUTH_GUARD.check(guard_key)
        if not require_code and cfg.captcha_enabled and not GLOBAL_CAPTCHA.validate_token(body.captcha_token or "", client_ip=client_ip):
            raise HTTPException(status_code=403, detail="captcha_required")
        try:
            result = auth_view(
                auth.register(
                    email=body.email,
                    password=body.password,
                    display_name=body.display_name,
                    verification_code=body.verification_code,
                    require_verification=require_code,
                    register_ip=client_ip,
                )
            )
            _AUTH_GUARD.reset(guard_key)
            return result
        except AccountError as exc:
            _AUTH_GUARD.record_failure(guard_key)
            status = 409 if exc.code == "account_email_exists" else 400
            raise HTTPException(status_code=status, detail=exc.code) from None

    @app.post("/auth/login", response_model=AuthSessionView)
    async def login(body: AuthCredentialsRequest, request: Request):
        auth = accounts(request)
        if auth is None:
            raise HTTPException(status_code=503, detail="unavailable")
        guard_key = f"login:{_client_ip(request)}:{body.email.strip().lower()}"
        _AUTH_GUARD.check(guard_key)
        try:
            result = auth_view(auth.login(email=body.email, password=body.password))
            _AUTH_GUARD.reset(guard_key)
            return result
        except AccountError as exc:
            _AUTH_GUARD.record_failure(guard_key)
            status = 401 if exc.code == "authentication_failed" else 403 if exc.code == "account_disabled" else 400
            raise HTTPException(status_code=status, detail=exc.code) from None

    @app.post("/auth/admin-login", response_model=AuthSessionView)
    async def admin_login(body: AuthCredentialsRequest, request: Request):
        auth = accounts(request)
        if auth is None:
            raise HTTPException(status_code=503, detail="unavailable")
        guard_key = f"admin:{_client_ip(request)}:{body.email.strip().lower()}"
        _AUTH_GUARD.check(guard_key)
        try:
            result = auth_view(auth.admin_login(email=body.email, password=body.password))
            _AUTH_GUARD.reset(guard_key)
            return result
        except AccountError as exc:
            _AUTH_GUARD.record_failure(guard_key)
            status = 401 if exc.code in {"authentication_failed", "admin_required"} else 403 if exc.code == "account_disabled" else 400
            raise HTTPException(status_code=status, detail=exc.code) from None

    @app.get("/auth/me", response_model=AccountView)
    async def me(request: Request, authorization: str | None = Header(default=None)):
        principal = current_account(request, authorization)
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication_required")
        return AccountView(
            id=principal.id,
            email=principal.email,
            display_name=principal.display_name,
            role=principal.role,
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

    def preference_service(request: Request) -> AccountPreferenceService:
        service_instance = getattr(request.app.state, "account_preference_service", None)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="unavailable")
        return service_instance

    def pref_error(exc: AccountPreferenceError) -> HTTPException:
        if exc.code == "command_trigger_exists":
            return HTTPException(status_code=409, detail=exc.code)
        if exc.code in {"command_not_found", "account_not_found"}:
            return HTTPException(status_code=404, detail=exc.code)
        return HTTPException(status_code=422, detail=exc.code)

    @app.patch("/account/profile", response_model=AccountView)
    async def update_account_profile(
        body: ProfileUpdateRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        auth = accounts(request)
        if auth is None:
            raise HTTPException(status_code=503, detail="unavailable")
        try:
            updated = auth.update_display_name(
                account_id=principal.id, display_name=body.display_name
            )
        except AccountError as exc:
            status = 401 if exc.code == "authentication_required" else 422
            raise HTTPException(status_code=status, detail=exc.code) from None
        return AccountView(
            id=updated.id,
            email=updated.email,
            display_name=updated.display_name,
            role=updated.role,
        )

    @app.post("/account/change-password")
    async def change_account_password(
        body: ChangePasswordRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        auth = accounts(request)
        if auth is None:
            raise HTTPException(status_code=503, detail="unavailable")
        try:
            auth.change_password(
                account_id=principal.id,
                current_password=body.current_password,
                new_password=body.new_password,
                keep_token=(authorization or "")[7:].strip() or None,
            )
        except AccountError as exc:
            status = 401 if exc.code in {"authentication_failed", "authentication_required"} else 422
            raise HTTPException(status_code=status, detail=exc.code) from None
        return {"status": "updated"}

    @app.get("/account/preferences", response_model=UserPreferencesView)
    async def get_account_preferences(
        request: Request, authorization: str | None = Header(default=None)
    ):
        principal = require_account(request, authorization)
        return UserPreferencesView(**preference_service(request).get_preferences(principal.id))

    @app.patch("/account/preferences", response_model=UserPreferencesView)
    async def update_account_preferences(
        body: UserPreferencesUpdateRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        try:
            updated = preference_service(request).update_preferences(
                principal.id, body.model_dump(exclude_unset=True)
            )
        except AccountPreferenceError as exc:
            raise pref_error(exc) from None
        return UserPreferencesView(**updated)

    @app.get("/account/commands", response_model=UserPromptCommandsResponse)
    async def list_account_commands(
        request: Request, authorization: str | None = Header(default=None)
    ):
        principal = require_account(request, authorization)
        items = preference_service(request).list_commands(principal.id)
        return UserPromptCommandsResponse(
            commands=[UserPromptCommandView(**item) for item in items]
        )

    @app.post("/account/commands", response_model=UserPromptCommandView, status_code=201)
    async def create_account_command(
        body: UserPromptCommandCreateRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        try:
            created = preference_service(request).create_command(
                account_id=principal.id,
                trigger=body.trigger,
                title=body.title,
                content=body.content,
                mode_hint=body.mode_hint,
            )
        except AccountPreferenceError as exc:
            raise pref_error(exc) from None
        return UserPromptCommandView(**created)

    @app.patch("/account/commands/{command_id}", response_model=UserPromptCommandView)
    async def update_account_command(
        command_id: str,
        body: UserPromptCommandUpdateRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        try:
            updated = preference_service(request).update_command(
                account_id=principal.id,
                command_id=command_id,
                updates=body.model_dump(exclude_unset=True),
            )
        except AccountPreferenceError as exc:
            raise pref_error(exc) from None
        return UserPromptCommandView(**updated)

    @app.delete("/account/commands/{command_id}", status_code=204)
    async def delete_account_command(
        command_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        principal = require_account(request, authorization)
        try:
            preference_service(request).delete_command(
                account_id=principal.id, command_id=command_id
            )
        except AccountPreferenceError as exc:
            raise pref_error(exc) from None
        return None

    @app.get("/account/summary", response_model=AccountSummaryResponse)
    async def get_account_summary(
        request: Request, authorization: str | None = Header(default=None)
    ):
        principal = require_account(request, authorization)
        try:
            summary = preference_service(request).account_summary(
                account_id=principal.id,
                admin_ops=getattr(request.app.state, "admin_operations_service", None),
                model_directory=getattr(request.app.state, "model_directory", None),
                maritime_office=getattr(request.app.state, "maritime_office", None),
            )
        except AccountPreferenceError as exc:
            raise pref_error(exc) from None
        return AccountSummaryResponse(**summary)

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
        active_conversations = [
            item
            for item in (getattr(project, "conversations", []) or [])
            if getattr(item, "archived_at", None) is None
        ]
        return WorkspaceProjectView(
            project_id=project.id,
            name=project.name,
            conversation_count=len(active_conversations),
            created_at=project.created_at,
            updated_at=project.updated_at,
        )

    def material_view(material, conversation_count: int = 0) -> WorkspaceMaterialView:
        return WorkspaceMaterialView(
            conversation_count=conversation_count,
            material_id=material.id,
            name=material.filename,
            media_type=material.media_type,
            size_bytes=material.size_bytes,
            status=material.status,
            checksum=material.checksum,
            created_at=material.created_at,
        )

    def workbench_run_view(run: RunEnvelope) -> WorkbenchRunView:
        from backend.application.workspace.progress import project_progress
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
            graph=run.graph,
            graph_id=run.graph_id,
            graph_version=run.graph_version,
            node_states=list(run.node_states.values()),
            current_node_id=run.current_node_id,
            current_attempt_id=run.current_attempt_id,
            runtime_policy=run.runtime_policy,
            paused_at=run.paused_at,
            resumed_at=run.resumed_at,
            continuation_guidance=list(run.continuation_guidance),
            active_elapsed_ms=run.active_elapsed_ms,
            wall_elapsed_ms=run.wall_elapsed_ms,
            context_compaction_count=run.context_compaction_count,
            stages=project_progress(run, app.state.event_hub.progress_events(run.run_id)) if app.state.event_hub else [],
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
        # The conversation is gone for good, so its Run history, events and
        # files go with it; none of it is readable afterwards.
        control = getattr(request.app.state, "control_service", None)
        if control is not None:
            try:
                run_ids = await control.discard_session(session_id=conversation_id)
            except SchedulerCommandError:
                run_ids = []
            hub = getattr(request.app.state, "event_hub", None)
            if hub is not None and run_ids:
                hub.discard_runs(run_ids)
        purge_conversation_files(settings, principal.id, conversation_id)
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
            compaction_count = progress.get("context_compaction_count", 0) if isinstance(progress, dict) else 0
            if not isinstance(compaction_count, int) or compaction_count < 0:
                compaction_count = 0
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
            model_snapshot = (
                ModelRunSnapshot.model_validate(stored_payload["model_snapshot"])
                if stored_payload.get("model_snapshot") is not None
                else None
            )
            stored_guidance = progress.get("continuation_guidance", []) if isinstance(progress, dict) else []
            _internal_confirmations = {
                "已确认升级当前会话为工作模式，请继续执行并交付所需文件。",
                "保持聊天模式，直接输出完整文本正文。",
            }
            updates = [
                text
                for text in stored_payload.get("facts", {}).get("user_answers", {}).values()
                if isinstance(text, str) and text not in _internal_confirmations
            ]
            updates.extend(text for text in stored_guidance if isinstance(text, str))
            messages.append(
                WorkspaceMessageView(
                    message_id=f"{run.run_id}:user",
                    role="user",
                    content=run.question,
                    updates=list(dict.fromkeys(updates)),
                    materials=[material_view(owned_materials[key]) for key in attached_ids if key in owned_materials],
                    created_at=run.created_at,
                    run_id=run.run_id,
                    status=run.status,
                        model_snapshot=model_snapshot,
                    artifacts=run_artifacts,
                    context_compaction_count=0,
                )
            )
            visible_answer = run.answer or run.partial_output
            recovery = recovery_for_state(
                status=run.status,
                error_code=run.error_code,
                partial_output=run.partial_output,
                execution_state=progress if isinstance(progress, dict) else None,
                retryable=False,
            )
            if visible_answer or run.status in {"failed", "cancelled", "paused"}:
                messages.append(
                    WorkspaceMessageView(
                        message_id=f"{run.run_id}:assistant",
                        role="assistant",
                        content=visible_answer or "",
                        created_at=run.completed_at or run.created_at,
                        run_id=run.run_id,
                        status=run.status,
                        model_snapshot=model_snapshot,
                        # Internal error codes remain in the diagnostic store;
                        # the conversation contract exposes only recovery.
                        error_code=None,
                        recovery=recovery,
                        sources=run_sources,
                        artifacts=run_artifacts,
                        context_compaction_count=compaction_count,
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
        # The paper-card corpus is a large JSONL file.  Building its offset
        # index is controlled disk I/O and must not pause the FastAPI event
        # loop (health checks and other conversations stay responsive while a
        # source detail is opened for the first time).
        try:
            document = await asyncio.to_thread(source_repository(request).get, source_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except OSError:
            raise HTTPException(status_code=503, detail="paper_original_unavailable") from None
        if document is None:
            raise HTTPException(status_code=404, detail="source_not_found")
        return WorkspaceSourceResponse.model_validate(document.model_dump(mode="json"))

    @app.get("/workspace/sources/{source_id}/original")
    async def paper_original(
        source_id: str,
        request: Request,
        sha256: str = Query(pattern=r"^[a-f0-9]{64}$"),
        authorization: str | None = Header(default=None),
    ):
        require_account(request, authorization)
        try:
            original = await asyncio.to_thread(source_repository(request).paper_file, source_id, sha256)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except OSError:
            raise HTTPException(status_code=503, detail="paper_original_unavailable") from None
        if original is None:
            raise HTTPException(status_code=404, detail="source_not_found")
        return FileResponse(original.path, media_type="application/pdf", filename=original.filename,
                            content_disposition_type="inline", headers={"Cache-Control": "private, no-store", "ETag": f'"{original.sha256}"'})

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
        run_facts = resolve_run_facts(request, principal, body.facts, conversation_mode)
        from backend.infrastructure.model.registry import ModelRegistry

        model_registry = ModelRegistry(settings, directory=model_directory(request))
        ops = getattr(request.app.state, "admin_operations_service", None)
        dir_instance = model_directory(request)
        if body.model_profile_id:
            try:
                revision = model_registry.resolve(body.model_profile_id)
            except PortError:
                raise HTTPException(status_code=422, detail="model_profile_invalid") from None
            profile = revision.profile
            if ops is not None and not ops.is_model_permitted(
                account_id=principal.id,
                account_role=principal.role,
                profile_id=profile.profile_id,
                access_scope=revision.access_scope,
            ):
                raise HTTPException(status_code=403, detail="model_not_permitted")
            if not profile.available:
                raise HTTPException(status_code=409, detail="model_profile_unavailable")
            selected_profile_id = profile.profile_id
        else:
            all_revisions = dir_instance.list_current()
            permitted_revisions = (
                ops.filter_model_revisions(
                    account_id=principal.id,
                    account_role=principal.role,
                    revisions=all_revisions,
                )
                if ops is not None
                else list(all_revisions)
            )
            if not permitted_revisions:
                if ops is not None and (
                    ops.account_grant(principal.id)["model_policy"] != "default"
                    or bool(all_revisions)
                ):
                    raise HTTPException(status_code=403, detail="model_not_permitted")
                raise HTTPException(status_code=409, detail="model_profile_unavailable")
            catalog = model_registry.catalog_view(
                profiles=[rev.profile for rev in permitted_revisions]
            )
            selected_profile_id = catalog.default_profile_id
            try:
                profile = model_registry.get(selected_profile_id)
            except PortError:
                raise HTTPException(status_code=422, detail="model_profile_invalid") from None
        selected_thinking = body.thinking_mode or profile.default_thinking_mode
        if selected_thinking not in profile.thinking_modes:
            raise HTTPException(status_code=422, detail="thinking_mode_unsupported")
        model_snapshot = ModelRunSnapshot(
            profile_id=profile.profile_id,
            provider=profile.provider,
            provider_revision_id=profile.provider_revision_id,
            profile_revision_id=profile.profile_revision_id,
            profile_revision=profile.revision,
            model_id=profile.model_id,
            protocol=profile.protocol,
            thinking_mode=selected_thinking,
            context_tokens=profile.context_tokens,
            max_generation_tokens=profile.max_generation_tokens,
            capabilities=profile.capabilities,
            checked_on=profile.checked_on,
        )
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
                    return await workbench_resume(
                        body.target_run_id,
                        WorkbenchResumeRequest(
                            conversation_id=conversation_id,
                            request_id=body.request_id,
                            answer=body.answer,
                            attached_material_ids=body.attached_material_ids,
                        ),
                        request,
                        authorization,
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
                            facts=run_facts,
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
                    thinking_mode=selected_thinking,
                    model_profile_id=body.model_profile_id,
                    model_snapshot=model_snapshot,
                    facts=run_facts,
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
        if run.account_id != principal.id or run.status != "succeeded":
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
                kind=item.kind,
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
        try:
            current = await service(request).run(
                session_id=body.conversation_id, run_id=run_id
            )
            pending_item = await service(request).request(
                session_id=body.conversation_id, request_id=body.request_id
            )
            if pending_item.run_id != run_id:
                raise SchedulerCommandError("input request not found")
            if current.status != "waiting_user" or pending_item.status != "pending":
                raise SchedulerCommandError("input request is not pending")
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        original = RunInput.model_validate(current.payload)
        payload: RunInput | None = None
        if body.attached_material_ids:
            ids = list(dict.fromkeys(original.material_ids + body.attached_material_ids))
            materials_svc = request.app.state.material_service
            if materials_svc is None:
                raise HTTPException(status_code=503, detail="material_service_unavailable")
            try:
                materials_svc.resolve(
                    account_id=principal.id,
                    session_id=body.conversation_id,
                    conversation_id=body.conversation_id,
                    material_ids=ids,
                )
            except MaterialServiceError as exc:
                raise HTTPException(status_code=404, detail=exc.code) from None
            original = original.model_copy(
                update={
                    "material_ids": ids,
                    "attached_material_ids": list(
                        dict.fromkeys(
                            original.attached_material_ids + body.attached_material_ids
                        )
                    ),
                }
            )
            payload = original
        effective_answer = body.answer
        if pending_item.kind == "mode_upgrade":
            if body.answer not in ("keep_chat", "upgrade_work"):
                raise HTTPException(status_code=422, detail="explicit_mode_choice_required")
            if body.answer == "keep_chat":
                facts = {**original.facts, "mode_upgrade_declined": True}
                constraints = list(
                    dict.fromkeys(
                        [
                            *original.constraints,
                            "用户确认保持只读聊天模式：不使用文件写入或编译工具，请在核实证据后直接在对话正文中交付完整内容。",
                        ]
                    )
                )
                payload = original.model_copy(
                    update={"facts": facts, "constraints": constraints}
                )
                effective_answer = "保持聊天模式，直接输出完整文本正文。"
            else:
                facts = {**original.facts, "task_mode": "work"}
                facts.pop("mode_upgrade_declined", None)
                payload = original.model_copy(update={"mode": "work", "facts": facts})
                effective_answer = "已确认升级当前会话为工作模式，请继续执行并交付所需文件。"
            if body.guidance and body.guidance.strip():
                effective_answer += f"\n用户补充要求：{body.guidance.strip()}"
        try:
            _, run = await service(request).resume(
                session_id=body.conversation_id,
                run_id=run_id,
                request_id=body.request_id,
                answer=effective_answer,
                payload=payload,
            )
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        sync_run(request, principal, run)
        return WorkbenchRunCommandResponse(run=workbench_run_view(run))

    @app.post("/workspace/runs/{run_id}/continue", response_model=WorkbenchRunCommandResponse)
    async def workbench_continue(run_id: str, body: WorkbenchRunControlRequest, request: Request, authorization: str | None = Header(default=None)):
        principal = require_conversation(request, body.conversation_id, authorization)
        payload = None
        if body.attached_material_ids:
            current = await service(request).run(
                session_id=body.conversation_id, run_id=run_id
            )
            original = RunInput.model_validate(current.payload)
            ids = list(dict.fromkeys(original.material_ids + body.attached_material_ids))
            materials = request.app.state.material_service
            if materials is None:
                raise HTTPException(status_code=503, detail="material_service_unavailable")
            try:
                materials.resolve(
                    account_id=principal.id,
                    session_id=body.conversation_id,
                    conversation_id=body.conversation_id,
                    material_ids=ids,
                )
            except MaterialServiceError as exc:
                raise HTTPException(status_code=404, detail=exc.code) from None
            payload = original.model_copy(
                update={
                    "material_ids": ids,
                    "attached_material_ids": list(
                        dict.fromkeys(
                            original.attached_material_ids + body.attached_material_ids
                        )
                    ),
                }
            )
        try:
            _, run = await service(request).continue_run(
                session_id=body.conversation_id,
                run_id=run_id,
                guidance=body.guidance,
                payload=payload,
            )
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

        conversation_mode = "chat"
        workspace_service = workspace(request)
        if workspace_service is not None:
            conversation_mode = workspace_service.get(
                account_id=principal.id, conversation_id=body.conversation_id
            ).mode
        run_facts = resolve_run_facts(request, principal, body.facts, conversation_mode)

        try:
            _, run = await service(request).steer(
                session_id=body.conversation_id,
                run_id=run_id,
                payload=RunInput(
                    text=body.text,
                    mode=conversation_mode,
                    facts=run_facts,
                    constraints=body.constraints,
                    material_ids=ids,
                    attached_material_ids=body.attached_material_ids,
                ),
            )
        except SchedulerCommandError as exc:
            raise _command_error(exc) from exc
        sync_run(request, principal, run)
        return WorkbenchRunCommandResponse(run=workbench_run_view(run))

    async def workbench_control(
        run_id: str,
        body: WorkbenchRunControlRequest,
        request: Request,
        *,
        cancel: bool,
        authorization: str | None,
    ):
        principal = require_conversation(request, body.conversation_id, authorization)
        try:
            action = service(request).cancel if cancel else service(request).pause
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
        "/workspace/runs/{run_id}/pause", response_model=WorkbenchRunCommandResponse
    )
    async def workbench_pause(
        run_id: str,
        body: WorkbenchRunControlRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        return await workbench_control(
            run_id, body, request, cancel=False, authorization=authorization
        )

    @app.post(
        "/workspace/runs/{run_id}/cancel", response_model=WorkbenchRunCommandResponse
    )
    async def workbench_cancel(
        run_id: str,
        body: WorkbenchRunControlRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        return await workbench_control(
            run_id, body, request, cancel=True, authorization=authorization
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

        def stream_authorized() -> bool:
            try:
                current_account(request, authorization, update_last_seen=False)
            except HTTPException as exc:
                if exc.status_code != 401:
                    raise
                return False
            return True

        async def stream() -> AsyncIterator[bytes]:
            request.app.state.active_sse_streams = getattr(request.app.state, "active_sse_streams", 0) + 1
            try:
                started = monotonic()
                last_emit = started
                try:
                    heartbeat_seconds = float(
                        os.environ.get("ZHOUXU_STREAM_HEARTBEAT_SECONDS", "25")
                    )
                except ValueError:
                    heartbeat_seconds = 25.0
                cursor = after_sequence
                last_status: str | None = None
                while monotonic() - started < timeout_seconds:
                    if await request.is_disconnected():
                        return
                    if not stream_authorized():
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
                                "emitted_at": event.emitted_at.isoformat(),
                                "error": (
                                    {"message": public_error_message(event.error)}
                                    if event.error
                                    else None
                                ),
                                "run": workbench_run_view(event.run).model_dump(mode="json")
                                if event.run
                                else None,
                            }
                            if not stream_authorized():
                                return
                            yield _json_line(payload)
                            last_emit = monotonic()
                            cursor = event.sequence
                    # Lifecycle is a snapshot at the current stream cursor.  It
                    # is emitted after buffered deltas so a terminal snapshot can
                    # never make the UI regress from completed to receiving.
                    state_key = (run.status, run.revision, tuple(item.get("source_id") for item in run.execution_state.get("source_refs", [])))
                    if state_key != last_status:
                        last_status = state_key
                        if not stream_authorized():
                            return
                        yield _json_line(
                            {
                                "type": "lifecycle",
                                "run_id": run.run_id,
                                "sequence": cursor,
                                "run": workbench_run_view(run).model_dump(mode="json"),
                            }
                        )
                        last_emit = monotonic()
                        sync_run(request, principal, run)
                    if hub is not None and hub.stream_events_since(run_id=run_id, after_sequence=cursor):
                        continue
                    if run.status in {
                        "succeeded",
                        "failed",
                        "cancelled",
                        "paused",
                        "waiting_user",
                    }:
                        return
                    if heartbeat_seconds > 0 and monotonic() - last_emit >= heartbeat_seconds:
                        if not stream_authorized():
                            return
                        # Blank newline resets Cloudflare's 100s idle proxy timeout and is skipped by NDJSON readers.
                        yield b"\n"
                        last_emit = monotonic()
                    await asyncio.sleep(0.1)
            finally:
                request.app.state.active_sse_streams = max(
                    0, getattr(request.app.state, "active_sse_streams", 1) - 1
                )

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.get("/workspace/materials", response_model=WorkspaceMaterialListResponse)
    async def list_workspace_materials(
        request: Request, authorization: str | None = Header(default=None)
    ):
        principal = require_account(request, authorization)
        service_instance = workspace(request)
        if service_instance is None:
            raise HTTPException(status_code=503, detail="workspace_service_unavailable")
        counts = service_instance.material_conversation_counts(account_id=principal.id)
        return WorkspaceMaterialListResponse(
            materials=[
                material_view(item, counts.get(item.id, 0))
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
        quota = _prepare_upload_quota(request, principal)
        content = await file.read(settings.materials.max_file_bytes + 1)
        try:
            material = materials.upload(
                account_id=principal.id,
                session_id=principal.id,
                filename=file.filename or "",
                media_type=file.content_type,
                content=content,
                quota_bytes=quota,
            )
        except MaterialServiceError as exc:
            status = 413 if exc.code in {"material_too_large", "material_quota_exceeded"} else 400
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
        quota = _prepare_upload_quota(request, principal)
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
                quota_bytes=quota,
            )
            service_instance.bind_material(
                account_id=principal.id,
                conversation_id=conversation_id,
                material_id=material.material_id,
            )
        except MaterialServiceError as exc:
            status = 413 if exc.code in {"material_too_large", "material_quota_exceeded"} else 400
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


    return app


app = create_app()
