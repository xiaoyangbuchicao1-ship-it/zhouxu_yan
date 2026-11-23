"""Public contracts for user account profile, preferences, slash commands, and summary."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from backend.contracts.schemas.core import Contract

ThinkingModeLiteral = Literal["none", "low", "medium", "high", "xhigh", "max"]


class UserPreferencesView(Contract):
    default_mode: Literal["chat", "work"] = "chat"
    default_thinking_mode: ThinkingModeLiteral = "none"
    concise_process: bool = False
    compact_density: bool = False
    reduced_motion: bool = False
    memory_enabled: bool = False
    memory_text: str = Field(default="", max_length=1200)
    updated_at: datetime | None = None


class UserPreferencesUpdateRequest(Contract):
    default_mode: Literal["chat", "work"] | None = None
    default_thinking_mode: ThinkingModeLiteral | None = None
    concise_process: bool | None = None
    compact_density: bool | None = None
    reduced_motion: bool | None = None
    memory_enabled: bool | None = None
    memory_text: str | None = Field(default=None, max_length=1200)


class ProfileUpdateRequest(Contract):
    display_name: str = Field(max_length=80)


class ChangePasswordRequest(Contract):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


class UserPromptCommandView(Contract):
    id: str
    trigger: str
    title: str
    content: str
    mode_hint: Literal["chat", "work"] | None = None
    is_builtin: bool = False
    created_at: datetime
    updated_at: datetime


class UserPromptCommandsResponse(Contract):
    commands: list[UserPromptCommandView]


class UserPromptCommandCreateRequest(Contract):
    trigger: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=4000)
    mode_hint: Literal["chat", "work"] | None = None


class UserPromptCommandUpdateRequest(Contract):
    trigger: str | None = Field(default=None, min_length=1, max_length=32)
    title: str | None = Field(default=None, min_length=1, max_length=80)
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    mode_hint: Literal["chat", "work"] | None = None


class AccountCapabilitiesSummary(Contract):
    model_policy: Literal["default", "allowlist", "disabled"] = "default"
    allowed_model_labels: list[str] = Field(default_factory=list)
    skill_policy: Literal["default", "builtin_only", "disabled"] = "default"
    can_upload_skill: bool = True


class AccountUsageSummary(Contract):
    project_count: int = Field(ge=0)
    conversation_count: int = Field(ge=0)
    material_count: int = Field(ge=0)
    custom_skill_count: int = Field(ge=0)


class AccountSummaryAccountView(Contract):
    id: str
    email: str
    display_name: str = ""
    role: Literal["user", "admin"] = "user"


class AccountSummaryResponse(Contract):
    account: AccountSummaryAccountView
    capabilities: AccountCapabilitiesSummary
    usage: AccountUsageSummary
