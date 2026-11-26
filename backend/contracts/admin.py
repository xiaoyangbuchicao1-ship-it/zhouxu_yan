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
    register_ip: str | None = None
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
    tasks_24h: int = Field(default=0, ge=0)
    failed_24h: int = Field(default=0, ge=0)
    active_tasks: int = Field(default=0, ge=0)
    recent_failed: list["AdminTaskView"] = Field(default_factory=list)
    stuck_tasks: list["AdminTaskView"] = Field(default_factory=list)


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
    query: str = ""
    status: Literal["active", "disabled"] | None = None
    role: Literal["user", "admin"] | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    total: int = Field(default=0, ge=0)
    total_pages: int = Field(default=1, ge=1)


class AdminOverviewResponse(Contract):
    overview: AdminOverviewView


class AdminAuditResponse(Contract):
    events: list[AdminAuditView]
    category_counts: dict[str, int] | None = None
    next_cursor: str | None = None


class AdminConversationSummaryView(Contract):
    id: str
    account_id: str
    title: str
    mode: Literal["chat", "work"]
    archived: bool
    run_count: int = Field(ge=0)
    latest_status: str | None = None
    latest_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AdminConversationAccountView(Contract):
    id: str
    email: str
    display_name: str = ""


class AdminConversationStatusCounts(Contract):
    all: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    active: int = Field(ge=0)
    failed: int = Field(ge=0)
    no_runs: int = Field(ge=0)


class AdminConversationListResponse(Contract):
    account: AdminConversationAccountView | None = None
    conversations: list[AdminConversationSummaryView]
    status_counts: AdminConversationStatusCounts | None = None
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


class AdminRunMaterialView(Contract):
    material_id: str
    filename: str
    media_type: str
    size_bytes: int = Field(ge=0)
    status: str


class AdminRunToolTraceView(Contract):
    tool_name: str
    tool_label: str
    query: str | None = None
    result_count: int | None = Field(default=None, ge=0)
    status: Literal["succeeded", "failed"] = "succeeded"
    summary: str | None = None


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
    first_delta_ms: int | None = Field(default=None, ge=0)
    elapsed_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    current_unit: str | None = None
    context_compaction_count: int = Field(default=0, ge=0)
    continuation_guidance: list[str] = Field(default_factory=list)
    source_summary: dict[str, int] = Field(default_factory=dict)
    nodes: list[AdminRunNodeView] = Field(default_factory=list)
    materials: list[AdminRunMaterialView] = Field(default_factory=list)
    tool_traces: list[AdminRunToolTraceView] = Field(default_factory=list)
    sources: list[AdminRunSourceView] = Field(default_factory=list)
    artifacts: list[AdminRunArtifactView] = Field(default_factory=list)


class AdminConversationDetailResponse(Contract):
    account: AdminConversationAccountView | None = None
    conversation: AdminConversationSummaryView
    runs: list[AdminRunLogView]
    next_cursor: str | None = None


class AdminProviderCreateRequest(Contract):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    notes: str = Field(default="", max_length=240)
    preset_type: str = Field(default="custom", max_length=32)
    default_protocol: str
    auth_style: str | None = None
    base_url: str = Field(min_length=1)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    credential_mode: Literal["env_ref", "encrypted_db"] | None = None
    credential_ref: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    enabled: bool = True


class AdminProviderView(Contract):
    id: str
    name: str
    notes: str = ""
    preset_type: str
    default_protocol: str
    auth_style: str
    base_url: str
    extra_headers: dict[str, str] = Field(default_factory=dict)
    credential_mode: str
    credential_ref: str | None = None
    api_key_hint: str | None = None
    enabled: bool
    credential_configured: bool = False
    revision: int = 1
    updated_at: datetime


class AdminProviderUpdateRequest(Contract):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=240)
    default_protocol: str | None = None
    auth_style: str | None = None
    base_url: str | None = None
    extra_headers: dict[str, str] | None = None
    credential_mode: Literal["env_ref", "encrypted_db"] | None = None
    credential_ref: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    enabled: bool | None = None


class AdminModelCreateRequest(Contract):
    model_config = ConfigDict(extra="forbid")
    profile_id: str | None = Field(default=None, min_length=1, max_length=160)
    provider_id: str = Field(min_length=1, max_length=64)
    upstream_model_id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=160)
    family: Literal["deepseek", "gpt", "claude", "gemini", "glm", "qwen", "kimi", "other"] | None = None
    protocol_override: str | None = None
    context_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    thinking_modes: list[Literal["none", "low", "medium", "high", "xhigh", "max"]] | None = None
    default_thinking: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = None
    capabilities: dict[str, str] = Field(default_factory=dict)
    enabled: bool = False
    access_scope: Literal["all", "admin_only", "allowlist"] = "all"
    is_default: bool = False
    sort_order: int = 100
    source: str = "admin"
    checked_on: str = "2026-11-22"


class AdminModelUpdateRequest(Contract):
    model_config = ConfigDict(extra="forbid")
    upstream_model_id: str | None = Field(default=None, min_length=1, max_length=200)
    label: str | None = Field(default=None, min_length=1, max_length=160)
    family: Literal["deepseek", "gpt", "claude", "gemini", "glm", "qwen", "kimi", "other"] | None = None
    protocol_override: str | None = None
    context_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    thinking_modes: list[Literal["none", "low", "medium", "high", "xhigh", "max"]] | None = None
    default_thinking: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = None
    capabilities: dict[str, str] | None = None
    enabled: bool | None = None
    access_scope: Literal["all", "admin_only", "allowlist"] | None = None
    is_default: bool | None = None
    sort_order: int | None = None


class AdminModelDirectoryResponse(Contract):
    credential_encryption_ready: bool = False
    local_hosts_allowed: bool = False
    providers: list[AdminProviderView]
    models: list["AdminModelView"]


class AdminModelView(Contract):
    profile_id: str
    provider_id: str
    upstream_model_id: str
    label: str
    family: str
    protocol_override: str | None = None
    protocol: str
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
    connectivity_status: Literal["unverified", "verified", "failed"] = "unverified"
    connectivity_checked_at: datetime | None = None
    connectivity_latency_ms: int | None = None
    connectivity_error: str | None = None
    revision: int
    updated_at: datetime


class AdminProviderTestRequest(Contract):
    model_config = ConfigDict(extra="forbid")
    model_id: str | None = Field(default=None, max_length=200)


class AdminDiscoveredModelView(Contract):
    id: str
    family: str
    label: str
    already_added: bool = False
    context_tokens: int = 131_072
    max_output_tokens: int = 16_384
    thinking_modes: list[str] = Field(default_factory=lambda: ["none"])
    default_thinking: str = "none"
    inferred_matched: bool = False
    inferred_source: str = "通用默认值"


class AdminProviderDiscoverResponse(Contract):
    supported: bool
    models: list[AdminDiscoveredModelView] = Field(default_factory=list)
    notice: str | None = None


class AdminProviderBalanceResponse(Contract):
    status: Literal["ok", "unsupported", "auth_failed", "unreachable"]
    supported: bool
    display: str | None = None
    currency: str | None = None
    total_balance: str | None = None
    granted_balance: str | None = None
    topped_up_balance: str | None = None
    used_amount: str | None = None
    source: str | None = None
    notice: str | None = None


class AdminModelInferResponse(Contract):
    model_id: str
    family: str
    context_tokens: int
    max_output_tokens: int
    thinking_modes: list[str]
    default_thinking: str
    matched: bool
    source_hint: str


class AdminConnectivityTestResponse(Contract):
    ok: bool
    connectivity_status: Literal["verified", "failed"]
    latency_ms: int | None = None
    http_status: int | None = None
    error_code: str | None = None


class AdminCapabilityView(Contract):
    account_id: str
    email: str
    display_name: str
    model_policy: Literal["default", "allowlist", "disabled"]
    allowed_models: list[str] = Field(default_factory=list)
    skill_policy: Literal["default", "builtin_only", "disabled"]
    can_upload_skill: bool
    material_quota_mb: int | None = None
    material_used_bytes: int = Field(default=0, ge=0)
    updated_at: datetime | None = None


class AdminCapabilityUpdateRequest(Contract):
    model_policy: Literal["default", "allowlist", "disabled"] | None = None
    allowed_models: list[str] | None = None
    skill_policy: Literal["default", "builtin_only", "disabled"] | None = None
    can_upload_skill: bool | None = None
    material_quota_mb: int | None = Field(default=None, ge=0, le=1_048_576)
    clear_material_quota: bool | None = None


class AdminCapabilitiesResponse(Contract):
    accounts: list[AdminCapabilityView]


class AdminSkillView(Contract):
    id: str
    name: str
    summary: str
    version: str
    visibility: str
    status: Literal["ready", "unavailable", "not_configured"]
    mode_scope: Literal["chat", "work"] | None = None
    owner_email: str | None = None
    updated_at: datetime | None = None


class AdminUploadedSkillView(Contract):
    id: str
    name: str
    summary: str
    version: int
    owner_id: str
    owner_email: str | None = None
    active: bool
    file_count: int = Field(ge=0)
    size_bytes: int = Field(ge=0)
    created_at: str
    updated_at: str


class AdminSkillsResponse(Contract):
    status: Literal["ready", "degraded", "unavailable"]
    total: int = Field(ge=0)
    skills: list[AdminSkillView]
    uploaded: list[AdminUploadedSkillView] = Field(default_factory=list)


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
    error_code: str | None = None


class AdminResourceHealthResponse(Contract):
    status: Literal["ready", "degraded", "unavailable"]
    sources: dict[str, dict]


class AdminRuntimeHealthResponse(Contract):
    status: Literal["ready", "degraded", "unavailable"]
    components: dict[str, dict]


class AdminSystemMetricsResponse(Contract):
    collected_at: datetime
    compute: dict
    opensearch: dict
    concurrency: dict
    http: dict
    model_calls: dict
    storage: dict
    capacity: dict


class AdminOpsSettingsView(Contract):
    credential_encryption_ready: bool = False
    email_verification_enabled: bool = False
    allow_password_reset: bool = True
    email_ready: bool = False
    resend_from_email: str = ""
    resend_from_name: str = "舟序 Zhouxu"
    resend_api_key_hint: str | None = None
    resend_key_configured: bool = False
    resend_key_source: str = "none"
    material_quota_mb: int = 1024
    auto_maintenance_enabled: bool = True
    backup_keep_days: int = 7
    updated_at: datetime | None = None


class AdminOpsSettingsUpdateRequest(Contract):
    model_config = ConfigDict(extra="forbid")
    email_verification_enabled: bool | None = None
    allow_password_reset: bool | None = None
    resend_from_email: str | None = None
    resend_from_name: str | None = None
    resend_api_key: str | None = Field(default=None, repr=False)
    clear_resend_api_key: bool | None = None
    material_quota_mb: int | None = Field(default=None, ge=0, le=1048576)
    auto_maintenance_enabled: bool | None = None
    backup_keep_days: int | None = Field(default=None, ge=1, le=90)


class AdminEmailTestRequest(Contract):
    model_config = ConfigDict(extra="forbid")
    to_email: str = Field(min_length=3, max_length=320)


class AdminEmailTestResponse(Contract):
    ok: bool
    message_id: str | None = None
    error_code: str | None = None
    detail: str | None = None


AdminOverviewView.model_rebuild()
