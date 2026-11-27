"""SQLAlchemy models for account identity and authenticated workbench sessions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AccountModel(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80), default="")
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="active")
    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    register_ip: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    sessions: Mapped[list["SessionModel"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    harness_sessions: Mapped[list["HarnessSessionModel"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    materials: Mapped[list["MaterialModel"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["ConversationModel"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    projects: Mapped[list["ProjectModel"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped[AccountModel] = relationship(back_populates="sessions")
    harness_sessions: Mapped[list["HarnessSessionModel"]] = relationship(
        back_populates="auth_session", cascade="all, delete-orphan"
    )


class BusinessAuditModel(Base):
    """A small, public-facing record of business administration actions.

    This table intentionally stores only the actor, target, action and safe
    change details.  Server diagnostics, credentials, model payloads and
    stack traces belong to the operations log and never cross this boundary.
    """

    __tablename__ = "business_audits"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    actor_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), index=True
    )
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True
    )
    resource_type: Mapped[str] = mapped_column(String(40), default="account")
    resource_id: Mapped[str | None] = mapped_column(String(160), index=True)
    outcome: Mapped[str] = mapped_column(String(24), default="succeeded")
    details_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ModelAccessGroupModel(Base):
    """Named model access group granting non-default models to assigned users."""

    __tablename__ = "model_access_groups"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(240), default="")
    allowed_models_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AccountCapabilityGrantModel(Base):
    """Per-account model and skill access policy.

    The policy is deliberately separate from the public account record.  The
    execution layer reads it when accepting a Run; hiding a selector is never
    treated as an authorization check.
    """

    __tablename__ = "account_capability_grants"

    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    model_policy: Mapped[str] = mapped_column(String(24), default="default")
    allowed_models_json: Mapped[str | None] = mapped_column(Text)
    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_access_groups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        default=None,
    )
    skill_policy: Mapped[str] = mapped_column(String(24), default="default")
    can_upload_skill: Mapped[bool] = mapped_column(Boolean, default=True)
    material_quota_mb: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ModelProviderModel(Base):
    """A user-visible provider connection, without a plaintext credential."""

    __tablename__ = "model_providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    notes: Mapped[str] = mapped_column(String(240), default="")
    preset_type: Mapped[str] = mapped_column(String(32), default="custom")
    default_protocol: Mapped[str] = mapped_column(String(64))
    auth_style: Mapped[str] = mapped_column(String(32), default="bearer")
    base_url: Mapped[str] = mapped_column(Text)
    extra_headers_json: Mapped[str | None] = mapped_column(Text)
    credential_mode: Mapped[str] = mapped_column(String(24), default="env_ref")
    credential_ref: Mapped[str | None] = mapped_column(String(120))
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    api_key_hint: Mapped[str | None] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    revisions: Mapped[list["ProviderRevisionModel"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )
    profiles: Mapped[list["ModelProfileModel"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan"
    )


class ProviderRevisionModel(Base):
    """Immutable provider facts used by one or more model revisions."""

    __tablename__ = "model_provider_revisions"
    __table_args__ = (
        UniqueConstraint("provider_id", "revision", name="uq_provider_revision"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("model_providers.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    default_protocol: Mapped[str] = mapped_column(String(64))
    auth_style: Mapped[str] = mapped_column(String(32), default="bearer")
    base_url: Mapped[str] = mapped_column(Text)
    credential_mode: Mapped[str] = mapped_column(String(24), default="env_ref")
    credential_ref: Mapped[str | None] = mapped_column(String(120))
    config_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    provider: Mapped[ModelProviderModel] = relationship(back_populates="revisions")


class ModelProfileModel(Base):
    """The single selectable model directory shared by chat and work modes."""

    __tablename__ = "model_profiles"

    profile_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("model_providers.id", ondelete="CASCADE"), index=True
    )
    upstream_model_id: Mapped[str] = mapped_column(String(200))
    label: Mapped[str] = mapped_column(String(160))
    family: Mapped[str] = mapped_column(String(32))
    protocol_override: Mapped[str | None] = mapped_column(String(64))
    context_tokens: Mapped[int] = mapped_column(Integer)
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    thinking_modes_json: Mapped[str] = mapped_column(Text, default='["none"]')
    default_thinking: Mapped[str] = mapped_column(String(16), default="none")
    capabilities_json: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    access_scope: Mapped[str] = mapped_column(String(24), default="all")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    verification_json: Mapped[str | None] = mapped_column(Text)
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(120), default="admin")
    checked_on: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    provider: Mapped[ModelProviderModel] = relationship(back_populates="profiles")
    revisions: Mapped[list["ModelProfileRevisionModel"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class ModelProfileRevisionModel(Base):
    """Immutable model facts paired with an immutable provider revision."""

    __tablename__ = "model_profile_revisions"
    __table_args__ = (
        UniqueConstraint("profile_id", "revision", name="uq_profile_revision"),
    )

    id: Mapped[str] = mapped_column(String(220), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("model_profiles.profile_id", ondelete="CASCADE"), index=True
    )
    provider_revision_id: Mapped[str] = mapped_column(
        ForeignKey("model_provider_revisions.id", ondelete="RESTRICT"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    facts_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    profile: Mapped[ModelProfileModel] = relationship(back_populates="revisions")


class ModelCallMetricModel(Base):
    """Durable, secret-free model call metrics for admin diagnostics."""

    __tablename__ = "model_call_metrics"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    provider_id: Mapped[str | None] = mapped_column(String(64), index=True)
    provider_revision_id: Mapped[str | None] = mapped_column(String(80), index=True)
    profile_id: Mapped[str | None] = mapped_column(String(160), index=True)
    profile_revision_id: Mapped[str | None] = mapped_column(String(220), index=True)
    protocol: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class HarnessSessionModel(Base):
    __tablename__ = "harness_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    auth_session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    account: Mapped[AccountModel] = relationship(back_populates="harness_sessions")
    auth_session: Mapped[SessionModel] = relationship(
        back_populates="harness_sessions"
    )


class MaterialModel(Base):
    __tablename__ = "materials"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(String(24), default="uploaded")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    account: Mapped[AccountModel] = relationship(back_populates="materials")


class ConversationModel(Base):
    """Account-owned workspace used as the Harness scheduler session key."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    # A conversation has one user-facing mode.  Individual Runs copy this
    # value when accepted so changing the conversation later cannot widen or
    # narrow an already running task.
    mode: Mapped[str] = mapped_column(String(16), default="chat")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    account: Mapped[AccountModel] = relationship(back_populates="conversations")
    project: Mapped["ProjectModel | None"] = relationship(back_populates="conversations")
    materials: Mapped[list["ConversationMaterialModel"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    runs: Mapped[list["ConversationRunModel"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class ProjectModel(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    account: Mapped[AccountModel] = relationship(back_populates="projects")
    conversations: Mapped[list[ConversationModel]] = relationship(
        back_populates="project"
    )


class ConversationMaterialModel(Base):
    """Join table that scopes an uploaded material to a conversation."""

    __tablename__ = "conversation_materials"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    material_id: Mapped[str] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), primary_key=True
    )

    conversation: Mapped[ConversationModel] = relationship(back_populates="materials")
    material: Mapped[MaterialModel] = relationship()


class ConversationRunModel(Base):
    """Durable lightweight Run history; scheduler remains the runtime source."""

    __tablename__ = "conversation_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    partial_output: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    client_request_id: Mapped[str | None] = mapped_column(String(128))
    retry_of_run_id: Mapped[str | None] = mapped_column(String(64))
    payload_json: Mapped[str | None] = mapped_column(Text)
    material_ids_json: Mapped[str | None] = mapped_column(Text)
    progress_json: Mapped[str | None] = mapped_column(Text)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    first_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_delta_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(80))
    model_profile_id: Mapped[str | None] = mapped_column(String(160), index=True)
    model_profile_revision_id: Mapped[str | None] = mapped_column(String(220), index=True)
    provider_revision_id: Mapped[str | None] = mapped_column(String(80), index=True)
    model_snapshot_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    conversation: Mapped[ConversationModel] = relationship(back_populates="runs")


class UserPreferenceModel(Base):
    """Per-account persistent workspace preferences and long-term user memory."""

    __tablename__ = "user_preferences"

    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    default_mode: Mapped[str] = mapped_column(String(16), default="chat")
    default_thinking_mode: Mapped[str] = mapped_column(String(16), default="none")
    concise_process: Mapped[bool] = mapped_column(Boolean, default=False)
    compact_density: Mapped[bool] = mapped_column(Boolean, default=False)
    reduced_motion: Mapped[bool] = mapped_column(Boolean, default=False)
    completion_sound_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    memory_text: Mapped[str] = mapped_column(Text, default="")
    commands_seeded: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserPromptCommandModel(Base):
    """Account-scoped '/' prompt templates shown in the workspace composer."""

    __tablename__ = "user_prompt_commands"
    __table_args__ = (
        UniqueConstraint("account_id", "trigger", name="uq_user_prompt_command_trigger"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    trigger: Mapped[str] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(String(80))
    content: Mapped[str] = mapped_column(Text)
    mode_hint: Mapped[str | None] = mapped_column(String(16))
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SystemSettingModel(Base):
    """Deployment-wide runtime settings managed cleanly from the admin console."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailVerificationCodeModel(Base):
    """Short-lived, hashed 6-digit email codes for signup and password reset."""

    __tablename__ = "email_verification_codes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(32), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    request_ip: Mapped[str] = mapped_column(String(64), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


Index("ix_sessions_account_active", SessionModel.account_id, SessionModel.revoked_at)
Index(
    "ix_harness_sessions_account_status",
    HarnessSessionModel.account_id,
    HarnessSessionModel.status,
)
Index("ix_materials_account_session", MaterialModel.account_id, MaterialModel.session_id)
Index("ix_conversations_account_active", ConversationModel.account_id, ConversationModel.archived_at)
Index("ix_conversations_account_project", ConversationModel.account_id, ConversationModel.project_id)
Index("ix_conversations_account_order", ConversationModel.account_id, ConversationModel.pinned, ConversationModel.updated_at)
Index("ix_conversation_runs_conversation_created", ConversationRunModel.conversation_id, ConversationRunModel.created_at)
Index(
    "uq_conversation_runs_idempotency",
    ConversationRunModel.conversation_id,
    ConversationRunModel.client_request_id,
    unique=True,
)
Index("ix_projects_account_order", ProjectModel.account_id, ProjectModel.updated_at)
Index(
    "ix_email_codes_lookup",
    EmailVerificationCodeModel.email,
    EmailVerificationCodeModel.purpose,
    EmailVerificationCodeModel.created_at,
)
class AdminAccountAliasModel(Base):
    """Per-admin private alias and short note for another account."""

    __tablename__ = "admin_account_aliases"

    admin_account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    target_account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    alias: Mapped[str] = mapped_column(String(80), default="")
    note: Mapped[str] = mapped_column(String(240), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


Index("ix_email_codes_ip_recent", EmailVerificationCodeModel.request_ip, EmailVerificationCodeModel.created_at)
Index("ix_admin_account_aliases_target", AdminAccountAliasModel.target_account_id)
