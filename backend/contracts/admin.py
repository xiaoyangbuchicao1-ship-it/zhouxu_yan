"""Public contracts for the business administrator console."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from backend.contracts.schemas.core import Contract


class AdminAccountView(Contract):
    id: str
    email: str
    display_name: str
    status: Literal["active", "disabled"]
    role: Literal["user", "admin"]
    created_at: datetime
    updated_at: datetime


class AdminAccountUpdateRequest(Contract):
    status: Literal["active", "disabled"] | None = None
    role: Literal["user", "admin"] | None = None


class AdminOverviewView(Contract):
    total_accounts: int = Field(ge=0)
    active_accounts: int = Field(ge=0)
    disabled_accounts: int = Field(ge=0)
    admin_accounts: int = Field(ge=0)
    audit_events: int = Field(ge=0)


class AdminAuditView(Contract):
    id: str
    action: str
    actor_id: str
    actor_email: str
    target_account_id: str | None = None
    target_email: str | None = None
    resource_type: str = "account"
    resource_id: str | None = None
    outcome: Literal["succeeded", "failed"] = "succeeded"
    details: dict = Field(default_factory=dict)
    created_at: datetime


class AdminAccountsResponse(Contract):
    accounts: list[AdminAccountView]


class AdminOverviewResponse(Contract):
    overview: AdminOverviewView


class AdminAuditResponse(Contract):
    events: list[AdminAuditView]


class AdminConversationSummaryView(Contract):
    id: str
    account_id: str
    title: str
    mode: Literal["chat", "work"]
    archived: bool
    run_count: int = Field(ge=0)
    latest_status: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminConversationListResponse(Contract):
    conversations: list[AdminConversationSummaryView]
    next_cursor: str | None = None


class AdminRunNodeView(Contract):
    id: str
    label: str
    status: str | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)


class AdminRunSourceView(Contract):
    """Human-readable evidence attached to a public run projection."""

    source_id: str
    kind: Literal["law", "case", "paper"] | None = None
    title: str
    locator: str | None = None
    section: str | None = None
    version_date: str | None = None
    version_label: str | None = None
    authors: str | None = None
    collection: str | None = None


class AdminRunArtifactView(Contract):
    """Safe deliverable metadata; storage paths and tool receipts stay private."""

    artifact_id: str
    name: str
    media_type: str
    size_bytes: int = Field(ge=0)
    sha256: str
    version: int = Field(default=1, ge=1)
    kind: Literal["document", "preview", "file"] = "file"
    preview_artifact_id: str | None = None


class AdminRunLogView(Contract):
    run_id: str
    user_question: str
    public_answer: str | None = None
    status: str
    model: str | None = None
    provider: str | None = None
    protocol: str | None = None
    thinking_mode: str | None = None
    created_at: datetime
    first_event_at: datetime | None = None
    first_delta_at: datetime | None = None
    completed_at: datetime | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    nodes: list[AdminRunNodeView] = Field(default_factory=list)
    sources: list[AdminRunSourceView] = Field(default_factory=list)
    artifacts: list[AdminRunArtifactView] = Field(default_factory=list)


class AdminConversationDetailResponse(Contract):
    conversation: AdminConversationSummaryView
    runs: list[AdminRunLogView]
    next_cursor: str | None = None


class AdminProviderCreateRequest(Contract):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    preset_type: str = Field(default="custom", max_length=32)
    default_protocol: str
    auth_style: str = "bearer"
    base_url: str = Field(min_length=1)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    credential_mode: Literal["env_ref", "encrypted_db", "file_ref"] = "env_ref"
    credential_ref: str | None = None
    enabled: bool = True


class AdminProviderView(Contract):
    id: str
    name: str
    preset_type: str
    default_protocol: str
    auth_style: str
    base_url: str
    credential_mode: str
    credential_ref: str | None = None
    enabled: bool
    credential_configured: bool = False
    revision: int = 1
    updated_at: datetime


class AdminProviderUpdateRequest(Contract):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    default_protocol: str | None = None
    auth_style: str | None = None
    base_url: str | None = None
    extra_headers: dict[str, str] | None = None
    credential_mode: Literal["env_ref", "encrypted_db", "file_ref"] | None = None
    credential_ref: str | None = None
    enabled: bool | None = None


class AdminModelCreateRequest(Contract):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=1, max_length=160)
    provider_id: str = Field(min_length=1, max_length=64)
    upstream_model_id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=160)
    family: Literal["deepseek", "gpt", "claude", "gemini", "glm", "other"]
    protocol_override: str | None = None
    context_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    thinking_modes: list[Literal["none", "low", "medium", "high", "xhigh", "max"]] = Field(default_factory=lambda: ["none"])
    default_thinking: Literal["none", "low", "medium", "high", "xhigh", "max"] = "none"
    capabilities: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    access_scope: str = "all"
    is_default: bool = False
    sort_order: int = 100
    source: str = "admin"
    checked_on: str = "2026-11-22"


class AdminModelUpdateRequest(Contract):
    model_config = ConfigDict(extra="forbid")
    upstream_model_id: str | None = Field(default=None, min_length=1, max_length=200)
    label: str | None = Field(default=None, min_length=1, max_length=160)
    family: Literal["deepseek", "gpt", "claude", "gemini", "glm", "other"] | None = None
    protocol_override: str | None = None
    context_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    thinking_modes: list[Literal["none", "low", "medium", "high", "xhigh", "max"]] | None = None
    default_thinking: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = None
    capabilities: dict[str, str] | None = None
    enabled: bool | None = None
    access_scope: str | None = None
    sort_order: int | None = None


class AdminModelDirectoryResponse(Contract):
    providers: list[AdminProviderView]
    models: list["AdminModelView"]


class AdminModelView(Contract):
    profile_id: str
    provider_id: str
    upstream_model_id: str
    label: str
    family: str
    protocol_override: str | None = None
    context_tokens: int
    max_output_tokens: int
    thinking_modes: list[str]
    default_thinking: str
    capabilities: dict[str, str] = Field(default_factory=dict)
    enabled: bool
    access_scope: str
    is_default: bool = False
    sort_order: int = 100
    source: str = "admin"
    checked_on: str | None = None
    revision: int
    updated_at: datetime


class AdminCapabilityView(Contract):
    account_id: str
    email: str
    display_name: str
    model_policy: Literal["default", "allowlist", "disabled"]
    allowed_models: list[str] = Field(default_factory=list)
    skill_policy: Literal["default", "builtin_only", "disabled"]
    can_upload_skill: bool
    updated_at: datetime | None = None


class AdminCapabilityUpdateRequest(Contract):
    model_policy: Literal["default", "allowlist", "disabled"] | None = None
    allowed_models: list[str] | None = None
    skill_policy: Literal["default", "builtin_only", "disabled"] | None = None
    can_upload_skill: bool | None = None


class AdminCapabilitiesResponse(Contract):
    accounts: list[AdminCapabilityView]


class AdminSkillView(Contract):
    id: str
    name: str
    summary: str
    version: str
    visibility: str
    status: Literal["ready", "unavailable", "not_configured"]
    owner_email: str | None = None
    updated_at: datetime | None = None


class AdminSkillsResponse(Contract):
    status: Literal["ready", "degraded", "unavailable"]
    total: int = Field(ge=0)
    skills: list[AdminSkillView]


class AdminTaskView(Contract):
    run_id: str
    account_id: str
    account_email: str
    conversation_id: str
    question: str
    status: str
    model: str | None = None
    provider: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)
    answer_chars: int = Field(ge=0)


class AdminTasksResponse(Contract):
    tasks: list[AdminTaskView]
    next_cursor: str | None = None


class AdminResourceHealthResponse(Contract):
    status: Literal["ready", "degraded", "unavailable"]
    sources: dict[str, dict]


class AdminRuntimeHealthResponse(Contract):
    status: Literal["ready", "degraded", "unavailable"]
    components: dict[str, dict]
