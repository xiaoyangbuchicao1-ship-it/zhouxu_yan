"""Administrator mutations for the shared provider/model directory."""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import select

from backend.application.admin import AdminService, AdminServiceError
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    ModelProfileModel,
    ModelProfileRevisionModel,
    ModelProviderModel,
    ProviderRevisionModel,
)
from backend.infrastructure.model.endpoint_policy import EndpointPolicyError, validate_model_endpoint


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_PROTOCOLS = {
    "deepseek_chat_completions",
    "openai_chat_completions",
    "openai_responses",
    "anthropic_messages",
    "google_generative_language",
}
_FAMILIES = {"deepseek", "gpt", "claude", "gemini", "glm", "other"}
_THINKING = {"none", "low", "medium", "high", "xhigh", "max"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ModelDirectoryAdminService:
    def __init__(self, database: Database):
        self.database = database

    def create_provider(self, *, actor_id: str, data: dict) -> dict:
        provider_id = self._required_id(data.get("id"), "provider_id_invalid")
        protocol = data.get("default_protocol")
        if protocol not in _PROTOCOLS:
            raise AdminServiceError("model_protocol_invalid")
        base_url = self._endpoint(data.get("base_url"))
        auth_style = data.get("auth_style", "bearer")
        if auth_style not in {"bearer", "x-api-key", "x-goog-api-key", "none"}:
            raise AdminServiceError("model_auth_style_invalid")
        now = _now()
        with self.database.session() as session:
            if session.get(ModelProviderModel, provider_id) is not None:
                raise AdminServiceError("provider_exists")
            provider = ModelProviderModel(
                id=provider_id,
                name=self._text(data.get("name"), "provider_name_required", 120),
                preset_type=self._text(data.get("preset_type", "custom"), "preset_type_invalid", 32),
                default_protocol=protocol,
                auth_style=auth_style,
                base_url=base_url,
                extra_headers_json=json.dumps(data.get("extra_headers", {}), ensure_ascii=False, separators=(",", ":")),
                credential_mode=data.get("credential_mode", "env_ref"),
                credential_ref=data.get("credential_ref"),
                enabled=bool(data.get("enabled", True)),
                created_at=now,
                updated_at=now,
            )
            if provider.credential_mode not in {"env_ref", "encrypted_db", "file_ref"}:
                raise AdminServiceError("credential_mode_invalid")
            session.add(provider)
            session.add(
                ProviderRevisionModel(
                    id=f"{provider_id}:r1",
                    provider_id=provider_id,
                    revision=1,
                    default_protocol=protocol,
                    auth_style=auth_style,
                    base_url=base_url,
                    credential_mode=provider.credential_mode,
                    credential_ref=provider.credential_ref,
                    config_json=json.dumps({}, separators=(",", ":")),
                    created_at=now,
                )
            )
            AdminService._add_audit(
                session,
                actor_id=actor_id,
                target_account_id=None,
                action="model.provider_created",
                resource_type="model_provider",
                resource_id=provider_id,
                details={"provider_id": provider_id, "protocol": protocol},
            )
            session.commit()
            return self._provider_view(provider)

    def create_profile(self, *, actor_id: str, data: dict) -> dict:
        profile_id = self._required_id(data.get("profile_id"), "profile_id_invalid")
        provider_id = self._required_id(data.get("provider_id"), "provider_id_invalid")
        protocol = data.get("protocol_override")
        if protocol is not None and protocol not in _PROTOCOLS:
            raise AdminServiceError("model_protocol_invalid")
        family = data.get("family")
        if family not in _FAMILIES:
            raise AdminServiceError("model_family_invalid")
        modes = self._modes(data.get("thinking_modes", ["none"]))
        default = data.get("default_thinking", modes[0])
        if default not in modes:
            raise AdminServiceError("thinking_mode_invalid")
        now = _now()
        with self.database.session() as session:
            provider = session.get(ModelProviderModel, provider_id)
            if provider is None:
                raise AdminServiceError("provider_not_found")
            if session.get(ModelProfileModel, profile_id) is not None:
                raise AdminServiceError("profile_exists")
            provider_revision = session.scalar(
                select(ProviderRevisionModel)
                .where(ProviderRevisionModel.provider_id == provider_id)
                .order_by(ProviderRevisionModel.revision.desc())
                .limit(1)
            )
            if provider_revision is None:
                raise AdminServiceError("provider_revision_missing")
            profile = ModelProfileModel(
                profile_id=profile_id,
                provider_id=provider_id,
                upstream_model_id=self._text(data.get("upstream_model_id"), "upstream_model_required", 200),
                label=self._text(data.get("label"), "model_label_required", 160),
                family=family,
                protocol_override=protocol,
                context_tokens=self._positive_int(data.get("context_tokens"), "context_tokens_invalid"),
                max_output_tokens=self._positive_int(data.get("max_output_tokens"), "max_output_tokens_invalid"),
                thinking_modes_json=json.dumps(modes, separators=(",", ":")),
                default_thinking=default,
                capabilities_json=json.dumps(data.get("capabilities", {}), separators=(",", ":")),
                enabled=bool(data.get("enabled", True)),
                access_scope=data.get("access_scope", "all"),
                is_default=bool(data.get("is_default", False)),
                sort_order=int(data.get("sort_order", 100)),
                current_revision=1,
                source=data.get("source", "admin"),
                checked_on=data.get("checked_on", "2026-11-22"),
                created_at=now,
                updated_at=now,
            )
            session.add(profile)
            session.add(
                ModelProfileRevisionModel(
                    id=f"{profile_id}:r1",
                    profile_id=profile_id,
                    provider_revision_id=provider_revision.id,
                    revision=1,
                    facts_json=json.dumps(self._facts(profile), ensure_ascii=False, separators=(",", ":")),
                    created_at=now,
                )
            )
            AdminService._add_audit(
                session,
                actor_id=actor_id,
                target_account_id=None,
                action="model.profile_created",
                resource_type="model_profile",
                resource_id=profile_id,
                details={"profile_id": profile_id, "provider_id": provider_id},
            )
            session.commit()
            return self._profile_view(profile, provider)

    def update_profile(self, *, actor_id: str, profile_id: str, data: dict) -> dict:
        with self.database.session() as session:
            profile = session.get(ModelProfileModel, profile_id)
            if profile is None:
                raise AdminServiceError("profile_not_found")
            provider = session.get(ModelProviderModel, profile.provider_id)
            if provider is None:
                raise AdminServiceError("provider_not_found")
            allowed = {"label", "context_tokens", "max_output_tokens", "thinking_modes", "default_thinking", "enabled", "sort_order", "access_scope", "capabilities"}
            unknown = set(data) - allowed
            if unknown:
                raise AdminServiceError("model_update_field_invalid")
            if "label" in data:
                profile.label = self._text(data["label"], "model_label_required", 160)
            if "context_tokens" in data:
                profile.context_tokens = self._positive_int(data["context_tokens"], "context_tokens_invalid")
            if "max_output_tokens" in data:
                profile.max_output_tokens = self._positive_int(data["max_output_tokens"], "max_output_tokens_invalid")
            modes = self._modes(data["thinking_modes"]) if "thinking_modes" in data else json.loads(profile.thinking_modes_json)
            if "default_thinking" in data:
                if data["default_thinking"] not in modes:
                    raise AdminServiceError("thinking_mode_invalid")
                profile.default_thinking = data["default_thinking"]
            elif profile.default_thinking not in modes:
                profile.default_thinking = modes[0]
            profile.thinking_modes_json = json.dumps(modes, separators=(",", ":"))
            if "enabled" in data:
                profile.enabled = bool(data["enabled"])
            if "sort_order" in data:
                profile.sort_order = int(data["sort_order"])
            if "access_scope" in data:
                profile.access_scope = str(data["access_scope"])
            if "capabilities" in data:
                profile.capabilities_json = json.dumps(data["capabilities"], separators=(",", ":"))
            next_revision = profile.current_revision + 1
            provider_revision = session.scalar(
                select(ProviderRevisionModel)
                .where(ProviderRevisionModel.provider_id == profile.provider_id)
                .order_by(ProviderRevisionModel.revision.desc())
                .limit(1)
            )
            if provider_revision is None:
                raise AdminServiceError("provider_revision_missing")
            profile.current_revision = next_revision
            profile.updated_at = _now()
            session.add(
                ModelProfileRevisionModel(
                    id=f"{profile_id}:r{next_revision}",
                    profile_id=profile_id,
                    provider_revision_id=provider_revision.id,
                    revision=next_revision,
                    facts_json=json.dumps(self._facts(profile), ensure_ascii=False, separators=(",", ":")),
                    created_at=profile.updated_at,
                )
            )
            AdminService._add_audit(
                session,
                actor_id=actor_id,
                target_account_id=None,
                action="model.profile_updated",
                resource_type="model_profile",
                resource_id=profile_id,
                details={"revision": next_revision},
            )
            session.commit()
            return self._profile_view(profile, provider)

    @staticmethod
    def _required_id(value, code: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise AdminServiceError(code)
        return value

    @staticmethod
    def _text(value, code: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
            raise AdminServiceError(code)
        return value.strip()

    @staticmethod
    def _positive_int(value, code: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AdminServiceError(code)
        return value

    @staticmethod
    def _modes(value) -> list[str]:
        if not isinstance(value, list) or not value or any(item not in _THINKING for item in value):
            raise AdminServiceError("thinking_modes_invalid")
        return list(dict.fromkeys(value))

    @staticmethod
    def _endpoint(value) -> str:
        if not isinstance(value, str) or not value.strip():
            raise AdminServiceError("endpoint_required")
        try:
            validate_model_endpoint(value.strip())
        except EndpointPolicyError as exc:
            raise AdminServiceError(str(exc)) from None
        return value.strip().rstrip("/")

    @staticmethod
    def _facts(profile: ModelProfileModel) -> dict:
        return {
            "context_confirmed": False,
            "profile_id": profile.profile_id,
            "model_id": profile.upstream_model_id,
            "protocol": profile.protocol_override,
        }

    @staticmethod
    def _provider_view(provider: ModelProviderModel) -> dict:
        return {
            "id": provider.id,
            "name": provider.name,
            "preset_type": provider.preset_type,
            "default_protocol": provider.default_protocol,
            "auth_style": provider.auth_style,
            "base_url": provider.base_url,
            "credential_mode": provider.credential_mode,
            "credential_ref": provider.credential_ref,
            "enabled": provider.enabled,
            "updated_at": provider.updated_at,
        }

    @staticmethod
    def _profile_view(profile: ModelProfileModel, provider: ModelProviderModel) -> dict:
        return {
            "profile_id": profile.profile_id,
            "provider_id": provider.id,
            "upstream_model_id": profile.upstream_model_id,
            "label": profile.label,
            "family": profile.family,
            "protocol_override": profile.protocol_override,
            "context_tokens": profile.context_tokens,
            "max_output_tokens": profile.max_output_tokens,
            "thinking_modes": json.loads(profile.thinking_modes_json),
            "default_thinking": profile.default_thinking,
            "enabled": profile.enabled,
            "access_scope": profile.access_scope,
            "revision": profile.current_revision,
            "updated_at": profile.updated_at,
        }


__all__ = ["ModelDirectoryAdminService", "ModelDirectoryError"]
