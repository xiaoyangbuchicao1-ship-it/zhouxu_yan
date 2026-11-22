"""Public contracts for the business administrator console."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

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


class AdminConversationDetailResponse(Contract):
    conversation: AdminConversationSummaryView
    runs: list[AdminRunLogView]
    next_cursor: str | None = None
