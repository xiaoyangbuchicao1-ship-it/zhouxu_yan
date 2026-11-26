"""HTTP contracts for the Harness control plane.

The API deliberately exposes lifecycle objects rather than business-specific
conversation fields. A client submits a RunInput, receives a Run id, and then
observes the same Run until it completes or waits for user input.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from backend.contracts.harness.artifacts import ArtifactReference
from backend.contracts.harness.lifecycle import (
    RunRecovery,
)
from backend.contracts.model_profiles import ModelRunSnapshot
from backend.contracts.modules.loop import ConversationMode, ThinkingMode
from backend.contracts.modules.materials import StoredMaterialRef
from backend.contracts.modules.sources import SourceDocument, SourceReference
from backend.contracts.runtime import NodeState, RunGraph, RunPolicy
from backend.contracts.schemas.core import Contract


class WorkbenchRunPayload(Contract):
    """Public Run input; server-side material paths never cross this boundary."""

    text: str = Field(min_length=1)
    mode: ConversationMode = "chat"
    thinking_mode: ThinkingMode | None = None
    model_profile_id: str | None = Field(default=None, min_length=1)
    model_snapshot: ModelRunSnapshot | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    material_ids: list[str] = Field(default_factory=list)
    attached_material_ids: list[str] = Field(default_factory=list)


class WorkbenchSubmitRunRequest(Contract):
    conversation_id: str = Field(min_length=1)
    client_request_id: str | None = Field(default=None, min_length=1, max_length=128)
    # Resume carries its answer in ``answer`` and does not submit a new text.
    # Queue and steer validate that text at the route boundary.
    text: str = ""
    thinking_mode: ThinkingMode | None = None
    model_profile_id: str | None = Field(default=None, min_length=1)
    facts: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    material_ids: list[str] = Field(default_factory=list)
    attached_material_ids: list[str] = Field(default_factory=list)
    retry_of_run_id: str | None = Field(default=None, min_length=1)
    dispatch_mode: Literal["queue", "steer", "resume"] = "queue"
    target_run_id: str | None = Field(default=None, min_length=1)
    request_id: str | None = Field(default=None, min_length=1)
    answer: Any | None = None


class WorkbenchErrorView(Contract):
    """A deliberately small public error projection."""

    message: str


class WorkbenchStageView(Contract):
    id: str
    label: str
    elapsed_ms: int | None = None
    active: bool = False
    details: list[str] = Field(default_factory=list)


class WorkbenchRunView(Contract):
    run_id: str
    conversation_id: str
    status: str
    revision: int
    payload: WorkbenchRunPayload
    pending_request_ids: list[str] = Field(default_factory=list)
    answers: dict[str, Any] = Field(default_factory=dict)
    output: str | None = None
    partial_output: str | None = None
    error: WorkbenchErrorView | None = None
    recovery: RunRecovery = Field(default_factory=RunRecovery)
    retry_of_run_id: str | None = None
    completed_units: int | None = None
    total_units: int | None = None
    current_unit: str | None = None
    sources: list[SourceReference] = Field(default_factory=list)
    artifacts: list[ArtifactReference] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    first_event_at: datetime | None = None
    first_delta_at: datetime | None = None
    completed_at: datetime | None = None
    last_sequence: int = 0
    graph: RunGraph | None = None
    graph_id: str | None = None
    graph_version: str | None = None
    node_states: list[NodeState] = Field(default_factory=list)
    current_node_id: str | None = None
    current_attempt_id: str | None = None
    runtime_policy: RunPolicy = Field(default_factory=RunPolicy)
    paused_at: datetime | None = None
    resumed_at: datetime | None = None
    continuation_guidance: list[str] = Field(default_factory=list)
    active_elapsed_ms: int = 0
    wall_elapsed_ms: int = 0
    context_compaction_count: int = Field(default=0, ge=0)
    stages: list[WorkbenchStageView] = Field(default_factory=list)


class WorkbenchRunCommandResponse(Contract):
    run: WorkbenchRunView


class WorkbenchSnapshotResponse(Contract):
    conversation_id: str
    status: str
    active_run_id: str | None = None
    queued_run_ids: list[str] = Field(default_factory=list)
    waiting_run_ids: list[str] = Field(default_factory=list)
    runs: list[WorkbenchRunView] = Field(default_factory=list)


class WorkbenchResumeRequest(Contract):
    conversation_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    answer: Any
    guidance: str | None = Field(default=None, max_length=50000)
    attached_material_ids: list[str] = Field(default_factory=list)


class WorkbenchRunControlRequest(Contract):
    conversation_id: str = Field(min_length=1)
    guidance: str | None = Field(default=None, max_length=50000)
    attached_material_ids: list[str] = Field(default_factory=list)


class WorkbenchSteerRequest(Contract):
    conversation_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    facts: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    material_ids: list[str] = Field(default_factory=list)
    attached_material_ids: list[str] = Field(default_factory=list)


class MaterialUploadResponse(Contract):
    material: StoredMaterialRef


class CaptchaChallengeResponse(Contract):
    challenge_id: str
    bg_image: str
    piece_image: str
    y: int
    width: int
    height: int
    piece_width: int
    piece_height: int


class CaptchaVerifyRequest(Contract):
    challenge_id: str = Field(min_length=1)
    x: int = Field(ge=0, le=500)
    duration_ms: int = Field(default=0, ge=0)
    trail: list[Any] = Field(default_factory=list)


class CaptchaVerifyResponse(Contract):
    verified: bool
    captcha_token: str | None = None
    error: str | None = None


class AuthCredentialsRequest(Contract):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1)
    display_name: str = Field(default="", max_length=80)
    verification_code: str = Field(default="", max_length=16)
    captcha_token: str | None = Field(default=None, max_length=128)


class AuthConfigResponse(Contract):
    email_verification_enabled: bool = False
    allow_password_reset: bool = False
    captcha_enabled: bool = True


class SendVerificationCodeRequest(Contract):
    email: str = Field(min_length=1, max_length=320)
    purpose: Literal["register", "reset_password"] = "register"
    captcha_token: str | None = Field(default=None, max_length=128)


class ResetPasswordRequest(Contract):
    email: str = Field(min_length=1, max_length=320)
    verification_code: str = Field(min_length=4, max_length=16)
    new_password: str = Field(min_length=1)


class AccountView(Contract):
    id: str
    email: str
    display_name: str
    role: Literal["user", "admin"] = "user"


class AuthSessionView(Contract):
    account: AccountView
    session: dict[str, str]


class WorkspaceConversationView(Contract):
    conversation_id: str
    project_id: str | None = None
    title: str
    mode: ConversationMode = "chat"
    pinned: bool
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    run_count: int = 0


class WorkspaceConversationListResponse(Contract):
    conversations: list[WorkspaceConversationView]


class CreateConversationRequest(Contract):
    title: str = Field(default="", max_length=200)
    project_id: str | None = Field(default=None, min_length=1)
    mode: ConversationMode = "chat"


class UpdateConversationRequest(Contract):
    title: str | None = Field(default=None, max_length=200)
    pinned: bool | None = None
    mode: ConversationMode | None = None


class WorkspaceConversationResponse(Contract):
    conversation: WorkspaceConversationView


class WorkspaceProjectView(Contract):
    project_id: str
    name: str
    conversation_count: int = 0
    created_at: datetime
    updated_at: datetime


class WorkspaceProjectListResponse(Contract):
    projects: list[WorkspaceProjectView]


class CreateProjectRequest(Contract):
    name: str = Field(min_length=1, max_length=120)


class UpdateProjectRequest(Contract):
    name: str = Field(min_length=1, max_length=120)


class MoveConversationRequest(Contract):
    project_id: str | None = None


class WorkspaceMaterialView(Contract):
    material_id: str
    name: str
    media_type: str
    size_bytes: int
    status: str
    checksum: str
    created_at: datetime
    # Only the library list fills this in: how many conversations use the file.
    conversation_count: int = Field(default=0, ge=0)


class WorkspaceMaterialListResponse(Contract):
    materials: list[WorkspaceMaterialView]


class WorkspacePassageView(Contract):
    id: str
    ordinal: int
    locator: str | None = None
    heading_path: list[str] | str | None = None
    text: str
    enabled: bool = True


class WorkspacePassageListResponse(Contract):
    chunks: list[WorkspacePassageView]
    next_cursor: str | None = None
    truncated: bool = False
    parse_status: str = "available"
    complete: bool = False
    notices: list[str] = Field(default_factory=list)


class WorkspaceMessageView(Contract):
    message_id: str
    role: str
    content: str
    created_at: datetime
    run_id: str | None = None
    model_snapshot: ModelRunSnapshot | None = None
    status: str | None = None
    error_code: str | None = None
    recovery: RunRecovery = Field(default_factory=RunRecovery)
    materials: list[WorkspaceMaterialView] = Field(default_factory=list)
    updates: list[str] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    artifacts: list[ArtifactReference] = Field(default_factory=list)
    context_compaction_count: int = Field(default=0, ge=0)


class WorkspaceConversationDetailResponse(Contract):
    conversation: WorkspaceConversationView
    messages: list[WorkspaceMessageView]
    materials: list[WorkspaceMaterialView]


class WorkspaceSourceResponse(SourceDocument):
    """One controlled, human-readable source body for the source viewer."""


class WorkbenchStreamEvent(Contract):
    run_id: str
    sequence: int
    type: str
    text: str | None = None
    stage: str | None = None
    label: str | None = None
    run: WorkbenchRunView | None = None
    error: Any | None = None


class WorkbenchInputRequestView(Contract):
    request_id: str
    run_id: str
    kind: str = "clarification"
    question: str
    options: list[dict[str, str]] = Field(default_factory=list)
    allow_free_text: bool = True
    required: bool = True
    status: str
    answer: Any | None = None


class WorkbenchInputRequestResponse(Contract):
    request: WorkbenchInputRequestView
