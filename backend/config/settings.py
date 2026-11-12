from __future__ import annotations

import os
import re
import tomllib
from io import StringIO
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr

from backend.contracts.model_context import (
    ContextPolicy,
    DeploymentLimits,
    ModelCapabilities,
)
from backend.contracts.schemas.core import Error, PortError

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class RuntimeSettings(BaseModel):
    max_rounds: int = Field(ge=1)
    max_retries: int = Field(ge=0)
    run_timeout_seconds: float = Field(gt=0)
    call_timeout_seconds: float = Field(gt=0)
    model_call_timeout_seconds: float | None = Field(default=None, gt=0)
    action_timeout_seconds: float | None = Field(default=None, gt=0)
    tool_history_items: int = Field(default=4, ge=0)
    tool_history_chars: int = Field(default=3000, ge=0)
    recoverable_tool_errors: list[str] = Field(default_factory=list)
    read_only_recoverable_errors: list[str] = Field(default_factory=list)
    no_progress_rounds: int = Field(default=2, ge=1)


class GenerationMode(BaseModel):
    output_tokens: int = Field(gt=0)
    call_timeout_seconds: float = Field(gt=0)


class ModelSettings(BaseModel):
    provider: Literal["deepseek"] = "deepseek"
    endpoint: str
    allowed_hosts: list[str]
    name: str
    timeout_seconds: float = Field(gt=0)
    temperature: float
    thinking_mode: Literal["none", "low", "high"] = "none"
    modes: dict[str, GenerationMode]
    credential_file: str
    credential_names: list[str]
    clarification_tool: str
    clarification_description: str
    decision_protocol: Literal["text", "explicit"] = "text"
    delivery_tool: str = "deliver_response"
    delivery_description: str = ""

    @property
    def thinking_enabled(self) -> bool:
        return self.thinking_mode != "none"

    @property
    def generation_mode(self) -> GenerationMode:
        return self.modes[self.thinking_mode]


class ProgressSettings(BaseModel):
    enabled: bool = True
    labels: dict[str, str] = Field(default_factory=dict)


class MaterialSettings(BaseModel):
    max_file_bytes: int = Field(gt=0)
    storage_root: str = Field(min_length=1)
    preview_chars: int = Field(gt=0)
    overview_units: int = Field(gt=0)
    read_chars: int = Field(gt=0)
    navigation_preview_chars: int = Field(gt=0)
    search_max_results: int = Field(gt=0)
    search_excerpt_chars: int = Field(gt=0)
    search_result_chars: int = Field(gt=0)
    read_tool_description: str = Field(min_length=1)
    search_tool_description: str = Field(min_length=1)
    navigation_hint: str = Field(min_length=1)
    max_inline_image_bytes: int = Field(gt=0)
    web_timeout_seconds: float = Field(gt=0)
    web_max_bytes: int = Field(gt=0)
    web_user_agent: str = Field(min_length=1)
    ocr_enabled: bool = False
    ocr_language: str = Field(min_length=1)
    cache_root: str = "data/files/derived/materials"
    table_rows_per_block: int = Field(default=40, ge=1)
    mineru_enabled: bool = False
    mineru_version: str = "4.1.4"
    mineru_tier: Literal["basic", "standard", "advanced"] = "standard"
    mineru_runtime_ready: bool = False
    mineru_runtime_revision: str = ""
    ocr_runtime_revision: str = ""
    mineru_python: str = (
        "backend/infrastructure/parsing/mineru_runtime/.venv/Scripts/python.exe"
    )
    mineru_timeout_seconds: float = Field(default=120, gt=0)


class ModelProfile(BaseModel):
    main_provider: Literal["deepseek", "relay"]
    review_provider: Literal["deepseek", "relay"]
    decision_protocol: Literal["text", "explicit"] = "text"
    main_effort: Literal["low", "medium", "high"] | None = None
    review_effort: Literal["low", "medium", "high"] | None = None


class ModelRoutingSettings(BaseModel):
    active_profile: str
    relay_config_file: str
    continuation_cache_runs: int = Field(ge=1)
    profiles: dict[str, ModelProfile]


class MemorySettings(BaseModel):
    max_recall_chars: int = Field(gt=0)
    max_recall_items: int | None = Field(default=None, ge=1)


class RagToolSettings(BaseModel):
    enabled: bool
    name: str
    description: str
    result_budget_chars: int = Field(ge=1)
    max_calls_per_run: int = Field(ge=0)


class DatabaseSettings(BaseModel):
    url: str = Field(min_length=1)
    session_ttl_seconds: int = Field(default=2_592_000, ge=900)
    cookie_name: str = Field(default="zhouxu_session", min_length=1)
    secure_cookie: bool = False


class AuthSettings(BaseModel):
    password_min_length: int = Field(default=12, ge=8)
    token_bytes: int = Field(default=32, ge=16, le=64)


class RagSettings(BaseModel):
    review_enabled: bool = False
    endpoint: str
    allowed_hosts: list[str]
    read_alias: str
    credential_file: str
    username: str
    password_name: str
    ca_file: str | None = None
    local_dev_certificate: bool = False
    search_timeout_seconds: float = Field(gt=0)
    total_timeout_seconds: float = Field(gt=0)
    max_rounds: int = Field(ge=1)
    default_limit: int = Field(ge=1)
    max_limit: int = Field(ge=1)
    candidates: int = Field(ge=1)
    rrf_k: int = Field(ge=1)
    max_query_chars: int = Field(ge=1)
    evidence_budget_chars: int = Field(ge=1)
    review_budget_chars: int = Field(ge=1)
    review_max_tokens: int = Field(ge=1)
    embedding_model: str
    embedding_revision: str | None = None
    embedding_local_only: bool
    embedding_device: str
    embedding_threads: int = Field(ge=1)
    embedding_max_tokens: int = Field(ge=1)
    embedding_dimensions: int = Field(ge=1)
    warmup_text: str
    allow_bm25_fallback: bool
    content_anchor_limit: int = Field(ge=1)
    content_anchor_max_chars: int = Field(ge=1)
    query_noise_terms: list[str] = Field(default_factory=list)
    max_query_expansions: int = Field(ge=0)
    max_article_span: int = Field(default=20, ge=1)
    document_name_max_chars: int = Field(default=100, ge=1)
    anchor_phrase_boost: float = Field(default=8, gt=0)
    anchor_terms_boost: float = Field(default=3, gt=0)
    review_prompt: str


class CaseSettings(BaseModel):
    enabled: bool = False
    snapshot_dir: str = "../data/cases/current"
    read_alias: str = "maritime_cases_read"
    candidates: int = Field(default=50, ge=10, le=1000)
    max_results: int = Field(default=10, ge=1, le=20)
    default_results: int = Field(default=6, ge=1, le=20)
    excerpt_chars: int = Field(default=650, ge=100, le=4000)
    read_chars: int = Field(default=5000, ge=100, le=12000)
    max_read_chars: int = Field(default=12000, ge=100, le=24000)
    result_budget_chars: int = Field(default=16000, ge=1000)
    search_description: str = ""
    read_description: str = ""


class ExecutionSettings(BaseModel):
    """Resource limits and provider selection for work-mode execution."""

    enabled: bool = True
    provider: Literal["docker", "native"] = "docker"
    image: str = "zhouxu-task-runtime:0.2"
    native_python: str | None = None
    native_bash: str | None = None
    workspace_root: str = "data/files/generated/run-workspaces"
    command_timeout_seconds: float = Field(default=40, gt=0, le=300)
    max_file_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_read_chars: int = Field(default=120_000, gt=0)
    max_output_chars: int = Field(default=30_000, gt=0)
    memory: str = "1g"
    cpus: str = "2"
    pids_limit: int = Field(default=128, ge=16, le=4096)
    tmpfs_size: str = "128m"


class Settings(BaseModel):
    runtime: RuntimeSettings
    model: ModelSettings
    context_policy: ContextPolicy = Field(default_factory=ContextPolicy)
    model_capabilities: dict[str, ModelCapabilities] = Field(default_factory=dict)
    deployment_limits: dict[str, DeploymentLimits] = Field(default_factory=dict)
    model_routing: ModelRoutingSettings | None = None
    progress: ProgressSettings = ProgressSettings()
    materials: MaterialSettings
    memory: MemorySettings
    database: DatabaseSettings
    auth: AuthSettings = AuthSettings()
    rag: RagSettings
    rag_tool: RagToolSettings
    cases: CaseSettings = Field(default_factory=CaseSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    prompts: dict[str, str]
    reflection: dict[str, str]
    application: dict[str, str]
    messages: dict[str, str]

    def error(self, code: str, *, retryable: bool = False) -> Error:
        return Error(code=code, message=self.messages[code], retryable=retryable)


def load_settings(path: Path | None = None) -> Settings:
    with (path or BACKEND_ROOT / "config" / "defaults.toml").open("rb") as stream:
        data = tomllib.load(stream)
    # The checked-in defaults keep Docker for local development.  A cloud
    # service can select the Docker-free provider without editing application
    # code or maintaining a second copy of the full TOML file.
    provider = os.environ.get("ZHOUXU_EXECUTION_PROVIDER", "").strip().lower()
    if path is None and provider:
        data.setdefault("execution", {})["provider"] = provider
    with (BACKEND_ROOT / "config" / "model_capabilities.toml").open("rb") as stream:
        profiles = tomllib.load(stream)
    data.setdefault("model_capabilities", profiles["models"])
    data.setdefault("deployment_limits", profiles.get("deployments", {}))
    return Settings.model_validate(data)


def load_key(settings: Settings) -> SecretStr:
    # Read only the explicitly selected credential file. Never execute/interpolate it.
    for name in settings.model.credential_names:
        if os.environ.get(name, "").strip():
            return SecretStr(os.environ[name].strip())
    path = Path(settings.model.credential_file)
    if not path.is_absolute():
        path = BACKEND_ROOT / path
    if path.is_file():
        raw = path.read_text(encoding="utf-8-sig").strip()
        if re.fullmatch(r"sk-[A-Za-z0-9_-]+", raw):
            return SecretStr(raw)
        values = dotenv_values(stream=StringIO(raw), interpolate=False)
        for name in settings.model.credential_names:
            if values.get(name):
                return SecretStr(values[name])
    raise PortError(settings.error("credential_missing"))
