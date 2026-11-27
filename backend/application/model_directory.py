"""Administrator mutations and connectivity checks for the model directory."""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import select, update

from backend.application.admin import AdminService, AdminServiceError
from backend.config.settings import load_key, load_settings
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    ModelProfileModel,
    ModelProfileRevisionModel,
    ModelProviderModel,
    ProviderRevisionModel,
)
from backend.infrastructure.model.credentials import (
    credential_encryption_ready,
    decrypt_api_key,
    encrypt_api_key,
    is_allowed_env_ref,
)
from backend.infrastructure.model.endpoint_policy import (
    EndpointPolicyError,
    local_model_hosts_enabled,
    validate_model_endpoint,
)
from backend.infrastructure.model.health import (
    discover_provider_models,
    probe_model_endpoint,
    probe_provider_balance,
)
from backend.infrastructure.model.inference import infer_model_defaults
from backend.infrastructure.model.registry import model_family
from backend.infrastructure.model.relay_config import load_relay_config


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_PROTOCOLS = {
    "deepseek_chat_completions",
    "openai_chat_completions",
    "openai_responses",
    "anthropic_messages",
    "google_generative_language",
}
_AUTH_STYLES = {"bearer", "x-api-key", "x-goog-api-key", "none"}
_FAMILIES = {"deepseek", "gpt", "claude", "gemini", "glm", "qwen", "kimi", "other"}
_THINKING = {"none", "low", "medium", "high", "xhigh", "max"}
_ACCESS_SCOPES = {"all", "admin_only", "allowlist"}
_BLOCKED_HEADERS = frozenset({
    "authorization",
    "x-api-key",
    "x-goog-api-key",
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "cookie",
    "proxy-authorization",
})


_OVERSEAS_MODEL_TOKENS = (
    "claude",
    "gpt-",
    "gpt4",
    "gpt5",
    "chatgpt",
    "o1-",
    "o3-",
    "o4-",
    "gemini",
    "grok",
    "llama",
    "mistral",
    "mixtral",
    "sonar",
    "perplexity",
    "command-r",
    "anthropic",
    "openai/",
)
_DOMESTIC_MODEL_TOKENS = (
    "deepseek",
    "qwen",
    "qwq",
    "tongyi",
    "glm",
    "chatglm",
    "zhipu",
    "moonshot",
    "kimi",
    "doubao",
    "ernie",
    "wenxin",
    "hunyuan",
    "minimax",
    "abab",
    "baichuan",
    "yi-",
    "step-",
    "spark",
    "xinghuo",
    "internlm",
    "sensechat",
)


def is_domestic_mainstream_model(
    provider: ModelProviderModel,
    upstream_model_id: str,
    label: str,
    family: str,
) -> bool:
    """Classify whether a model belongs to Chinese domestic mainstream models."""

    haystack = f"{upstream_model_id} {label}".lower()
    tail = upstream_model_id.lower().rsplit("/", 1)[-1]
    if family in {"claude", "gpt", "gemini"}:
        return False
    if tail in {"o1", "o3", "o4"} or any(token in haystack for token in _OVERSEAS_MODEL_TOKENS):
        return False
    if family in {"deepseek", "qwen", "glm", "kimi"}:
        return True
    if any(token in haystack for token in _DOMESTIC_MODEL_TOKENS):
        return True
    return provider.preset_type in {"deepseek", "volcengine"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_dict(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _connectivity_from(raw: str | None) -> dict:
    data = _json_dict(raw).get("connectivity")
    return data if isinstance(data, dict) else {}


def _unverified_payload(
    existing_raw: str | None,
    *,
    provider_revision_id: str,
    profile_revision: int,
) -> dict:
    payload = _json_dict(existing_raw)
    payload["connectivity"] = {
        "status": "unverified",
        "checked_at": None,
        "latency_ms": None,
        "http_status": None,
        "error_code": None,
        "provider_revision_id": provider_revision_id,
        "profile_revision": profile_revision,
    }
    return payload


def _default_auth_style(protocol: str) -> str:
    if protocol == "anthropic_messages":
        return "x-api-key"
    if protocol == "google_generative_language":
        return "x-goog-api-key"
    return "bearer"


def _slug_part(raw: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-z0-9._:-]+", "-", raw.strip().lower()).strip("-.:_")
    if cleaned and cleaned[0].isalnum():
        return cleaned[:48]
    return f"{fallback}-{secrets.token_hex(3)}"


class ModelDirectoryAdminService:
    def __init__(self, database: Database):
        self.database = database

    def create_provider(self, *, actor_id: str, data: dict) -> dict:
        name = self._text(data.get("name"), "provider_name_required", 120)
        notes = str(data.get("notes") or "").strip()[:240]
        raw_id = data.get("id")
        if raw_id is not None and str(raw_id).strip():
            provider_id = self._required_id(raw_id.strip(), "provider_id_invalid")
        else:
            preset_seed = str(data.get("preset_type") or "").strip().lower()
            base_slug = _slug_part(preset_seed if preset_seed and preset_seed != "custom" else name, "provider")
            provider_id = f"{base_slug}-{secrets.token_hex(3)}"
        protocol = data.get("default_protocol")
        if protocol not in _PROTOCOLS:
            raise AdminServiceError("model_protocol_invalid")
        base_url = self._endpoint(data.get("base_url"))
        raw_auth = data.get("auth_style")
        auth_style = _default_auth_style(protocol) if raw_auth is None else raw_auth
        if auth_style not in _AUTH_STYLES:
            raise AdminServiceError("model_auth_style_invalid")
        extra_headers = self._headers(data.get("extra_headers", {}))
        raw_mode = data.get("credential_mode")
        if raw_mode is None:
            credential_mode = "env_ref" if data.get("credential_ref") else "encrypted_db"
        else:
            credential_mode = raw_mode
        if credential_mode not in {"env_ref", "encrypted_db"}:
            raise AdminServiceError("credential_mode_invalid")

        credential_ref: str | None = None
        api_key_encrypted: str | None = None
        api_key_hint: str | None = None
        if credential_mode == "env_ref":
            if data.get("api_key"):
                raise AdminServiceError("api_key_not_allowed_for_env_ref")
            if auth_style != "none":
                credential_ref = self._env_ref(data.get("credential_ref"))
        else:
            if auth_style != "none":
                api_key_encrypted, api_key_hint = self._encrypt_key(data.get("api_key"))

        now = _now()
        with self.database.session() as session:
            self._require_admin(session, actor_id)
            if session.get(ModelProviderModel, provider_id) is not None:
                raise AdminServiceError("provider_exists")
            provider = ModelProviderModel(
                id=provider_id,
                name=name,
                notes=notes,
                preset_type=self._text(data.get("preset_type", "custom"), "preset_type_invalid", 32),
                default_protocol=protocol,
                auth_style=auth_style,
                base_url=base_url,
                extra_headers_json=json.dumps(extra_headers, ensure_ascii=False, separators=(",", ":")),
                credential_mode=credential_mode,
                credential_ref=credential_ref,
                api_key_encrypted=api_key_encrypted,
                api_key_hint=api_key_hint,
                enabled=bool(data.get("enabled", True)),
                created_at=now,
                updated_at=now,
            )
            session.add(provider)
            config_payload: dict = {"extra_headers": extra_headers}
            if api_key_encrypted is not None:
                config_payload["api_key_encrypted"] = api_key_encrypted
            session.add(
                ProviderRevisionModel(
                    id=f"{provider_id}:r1",
                    provider_id=provider_id,
                    revision=1,
                    default_protocol=protocol,
                    auth_style=auth_style,
                    base_url=base_url,
                    credential_mode=credential_mode,
                    credential_ref=credential_ref,
                    config_json=json.dumps(config_payload, ensure_ascii=False, separators=(",", ":")),
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
                details={
                    "provider_id": provider_id,
                    "protocol": protocol,
                    "credential_mode": credential_mode,
                },
            )
            session.commit()
            return self._provider_view(provider, revision=1)

    def list_directory(self, *, actor_id: str) -> dict:
        with self.database.session() as session:
            self._require_admin(session, actor_id)
            providers = session.scalars(
                select(ModelProviderModel)
                .where(ModelProviderModel.archived_at.is_(None))
                .order_by(ModelProviderModel.created_at, ModelProviderModel.id)
            ).all()
            provider_map = {provider.id: provider for provider in providers}
            models = session.scalars(
                select(ModelProfileModel)
                .where(
                    ModelProfileModel.archived_at.is_(None),
                    ModelProfileModel.provider_id.in_(list(provider_map)),
                )
                .order_by(ModelProfileModel.sort_order, ModelProfileModel.profile_id)
            ).all() if provider_map else []
            revisions = {
                provider.id: self._provider_revision(session, provider.id)
                for provider in providers
            }
            return {
                "credential_encryption_ready": credential_encryption_ready(),
                "local_hosts_allowed": local_model_hosts_enabled(),
                "providers": [
                    self._provider_view(provider, revision=revisions[provider.id].revision)
                    for provider in providers
                ],
                "models": [
                    self._profile_view(model, provider_map[model.provider_id])
                    for model in models
                ],
            }

    def update_provider(self, *, actor_id: str, provider_id: str, data: dict) -> dict:
        allowed = {
            "name",
            "notes",
            "default_protocol",
            "auth_style",
            "base_url",
            "extra_headers",
            "credential_mode",
            "credential_ref",
            "api_key",
            "enabled",
        }
        if set(data) - allowed:
            raise AdminServiceError("provider_update_field_invalid")
        with self.database.session() as session:
            self._require_admin(session, actor_id)
            provider = session.get(ModelProviderModel, provider_id)
            if provider is None or provider.archived_at is not None:
                raise AdminServiceError("provider_not_found")

            connection_changed = False
            if "name" in data:
                provider.name = self._text(data["name"], "provider_name_required", 120)
            if "notes" in data:
                provider.notes = str(data["notes"] or "").strip()[:240]
            if "default_protocol" in data:
                if data["default_protocol"] not in _PROTOCOLS:
                    raise AdminServiceError("model_protocol_invalid")
                if data["default_protocol"] != provider.default_protocol:
                    connection_changed = True
                provider.default_protocol = data["default_protocol"]
            if "auth_style" in data and data["auth_style"] is not None:
                if data["auth_style"] not in _AUTH_STYLES:
                    raise AdminServiceError("model_auth_style_invalid")
                if data["auth_style"] != provider.auth_style:
                    connection_changed = True
                provider.auth_style = data["auth_style"]
            elif "default_protocol" in data and provider.auth_style != "none":
                derived_auth = _default_auth_style(provider.default_protocol)
                if derived_auth != provider.auth_style:
                    provider.auth_style = derived_auth
                    connection_changed = True
            if "base_url" in data:
                next_url = self._endpoint(data["base_url"])
                if next_url != provider.base_url:
                    connection_changed = True
                provider.base_url = next_url
            if "extra_headers" in data:
                next_headers = self._headers(data["extra_headers"])
                old_headers = _json_dict(provider.extra_headers_json)
                if next_headers != old_headers:
                    connection_changed = True
                provider.extra_headers_json = json.dumps(
                    next_headers, ensure_ascii=False, separators=(",", ":")
                )

            supplied_key = data.get("api_key")
            has_new_key = isinstance(supplied_key, str) and bool(supplied_key.strip())
            if "credential_mode" in data and data["credential_mode"] is not None:
                next_mode = data["credential_mode"]
            elif has_new_key:
                next_mode = "encrypted_db"
            else:
                next_mode = provider.credential_mode
            if next_mode != provider.credential_mode:
                connection_changed = True
            if "credential_mode" in data and data["credential_mode"] is not None and next_mode not in {"env_ref", "encrypted_db"}:
                raise AdminServiceError("credential_mode_invalid")
            if next_mode not in {"env_ref", "encrypted_db", "file_ref"}:
                raise AdminServiceError("credential_mode_invalid")
            if next_mode == "env_ref":
                if supplied_key:
                    raise AdminServiceError("api_key_not_allowed_for_env_ref")
                next_ref = data.get("credential_ref", provider.credential_ref)
                if provider.auth_style != "none":
                    next_ref = self._env_ref(next_ref)
                if next_ref != provider.credential_ref:
                    connection_changed = True
                provider.credential_mode = "env_ref"
                provider.credential_ref = next_ref
                provider.api_key_encrypted = None
                provider.api_key_hint = None
            elif next_mode == "encrypted_db":
                if provider.auth_style != "none":
                    if has_new_key:
                        encrypted, hint = self._encrypt_key(supplied_key)
                        provider.api_key_encrypted = encrypted
                        provider.api_key_hint = hint
                        connection_changed = True
                    elif not provider.api_key_encrypted:
                        raise AdminServiceError("api_key_required")
                provider.credential_mode = "encrypted_db"
                provider.credential_ref = None
            else:
                # Seeded file_ref providers retain read-only credential_ref when mode is unchanged.
                if supplied_key:
                    raise AdminServiceError("api_key_not_allowed_for_env_ref")
                if "credential_ref" in data and data["credential_ref"] != provider.credential_ref:
                    raise AdminServiceError("credential_mode_invalid")

            if "enabled" in data:
                provider.enabled = bool(data["enabled"])

            current_revision = self._provider_revision(session, provider_id)
            next_revision = current_revision.revision + 1
            now = _now()
            provider.updated_at = now
            config_payload: dict = {"extra_headers": _json_dict(provider.extra_headers_json)}
            if provider.credential_mode == "encrypted_db" and provider.api_key_encrypted:
                config_payload["api_key_encrypted"] = provider.api_key_encrypted
            provider_revision = ProviderRevisionModel(
                id=f"{provider_id}:r{next_revision}",
                provider_id=provider_id,
                revision=next_revision,
                default_protocol=provider.default_protocol,
                auth_style=provider.auth_style,
                base_url=provider.base_url,
                credential_mode=provider.credential_mode,
                credential_ref=provider.credential_ref,
                config_json=json.dumps(config_payload, ensure_ascii=False, separators=(",", ":")),
                created_at=now,
            )
            session.add(provider_revision)

            profiles = session.scalars(
                select(ModelProfileModel).where(
                    ModelProfileModel.provider_id == provider_id,
                    ModelProfileModel.archived_at.is_(None),
                )
            ).all()
            for profile in profiles:
                profile.current_revision += 1
                profile.updated_at = now
                if connection_changed:
                    profile.enabled = False
                    profile.is_default = False
                    profile.verification_json = json.dumps(
                        _unverified_payload(
                            profile.verification_json,
                            provider_revision_id=provider_revision.id,
                            profile_revision=profile.current_revision,
                        ),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                else:
                    verification = _json_dict(profile.verification_json)
                    connectivity = verification.get("connectivity")
                    if isinstance(connectivity, dict) and connectivity.get("status") == "verified":
                        connectivity["provider_revision_id"] = provider_revision.id
                        connectivity["profile_revision"] = profile.current_revision
                        profile.verification_json = json.dumps(
                            verification, ensure_ascii=False, separators=(",", ":")
                        )
                session.add(
                    ModelProfileRevisionModel(
                        id=f"{profile.profile_id}:r{profile.current_revision}",
                        profile_id=profile.profile_id,
                        provider_revision_id=provider_revision.id,
                        revision=profile.current_revision,
                        facts_json=json.dumps(
                            self._facts(profile), ensure_ascii=False, separators=(",", ":")
                        ),
                        created_at=now,
                    )
                )
            AdminService._add_audit(
                session,
                actor_id=actor_id,
                target_account_id=None,
                action="model.provider_updated",
                resource_type="model_provider",
                resource_id=provider_id,
                details={
                    "revision": next_revision,
                    "profiles_advanced": len(profiles),
                    "connectivity_reset": connection_changed,
                },
            )
            session.commit()
            return self._provider_view(provider, revision=next_revision)

    def create_profile(self, *, actor_id: str, data: dict) -> dict:
        provider_id = self._required_id(data.get("provider_id"), "provider_id_invalid")
        upstream_model_id = self._text(
            data.get("upstream_model_id"), "upstream_model_required", 200
        )
        raw_profile_id = data.get("profile_id")
        if raw_profile_id is not None and str(raw_profile_id).strip():
            profile_id = self._required_id(raw_profile_id.strip(), "profile_id_invalid")
        else:
            profile_id = f"{provider_id}:{_slug_part(upstream_model_id, 'model')}"[:160]
        protocol = data.get("protocol_override")
        if protocol is not None and protocol not in _PROTOCOLS:
            raise AdminServiceError("model_protocol_invalid")
        raw_family = data.get("family")
        if raw_family is not None:
            if raw_family not in _FAMILIES:
                raise AdminServiceError("model_family_invalid")
            family = raw_family
        else:
            family = None
        inferred_defaults = infer_model_defaults(upstream_model_id)
        raw_modes = data.get("thinking_modes")
        modes = self._modes(list(inferred_defaults.thinking_modes) if raw_modes is None else raw_modes)
        raw_default_thinking = data.get("default_thinking")
        if raw_default_thinking is None:
            default = (
                inferred_defaults.default_thinking
                if inferred_defaults.default_thinking in modes
                else modes[0]
            )
        else:
            default = raw_default_thinking
        if default not in modes:
            raise AdminServiceError("thinking_mode_invalid")
        raw_context_tokens = data.get("context_tokens")
        context_tokens = (
            inferred_defaults.context_tokens
            if raw_context_tokens is None
            else self._positive_int(raw_context_tokens, "context_tokens_invalid")
        )
        raw_max_output = data.get("max_output_tokens")
        max_output_tokens = (
            inferred_defaults.max_output_tokens
            if raw_max_output is None
            else self._positive_int(raw_max_output, "max_output_tokens_invalid")
        )
        raw_access_scope = data.get("access_scope")
        if raw_access_scope is not None:
            access_scope = str(raw_access_scope)
            if access_scope not in _ACCESS_SCOPES:
                raise AdminServiceError("access_scope_invalid")
        else:
            access_scope = None
        if bool(data.get("enabled", False)) or bool(data.get("is_default", False)):
            raise AdminServiceError("model_connectivity_unverified")

        now = _now()
        with self.database.session() as session:
            self._require_admin(session, actor_id)
            provider = session.get(ModelProviderModel, provider_id)
            if provider is None or provider.archived_at is not None:
                raise AdminServiceError("provider_not_found")
            if raw_profile_id is None or not str(raw_profile_id).strip():
                candidate = profile_id
                suffix = 2
                while session.get(ModelProfileModel, candidate) is not None:
                    tail = f"-{suffix}"
                    candidate = f"{profile_id[:160 - len(tail)]}{tail}"
                    suffix += 1
                profile_id = candidate
            elif session.get(ModelProfileModel, profile_id) is not None:
                raise AdminServiceError("profile_exists")
            if family is None:
                inferred = model_family(upstream_model_id)
                if inferred == "other":
                    effective_proto = protocol or provider.default_protocol
                    inferred = {
                        "anthropic_messages": "claude",
                        "google_generative_language": "gemini",
                        "deepseek_chat_completions": "deepseek",
                    }.get(effective_proto, "other")
                family = inferred if inferred in _FAMILIES else "other"
            label_text = self._text(data.get("label"), "model_label_required", 160)
            resolved_scope = (
                access_scope
                if access_scope is not None
                else (
                    "all"
                    if is_domestic_mainstream_model(
                        provider, upstream_model_id, label_text, family
                    )
                    else "allowlist"
                )
            )
            provider_revision = self._provider_revision(session, provider_id)
            verification = _unverified_payload(
                None,
                provider_revision_id=provider_revision.id,
                profile_revision=1,
            )
            raw_caps = dict(data.get("capabilities") or {})
            if "vision" not in raw_caps:
                raw_caps["vision"] = (
                    "confirmed"
                    if infer_model_defaults(upstream_model_id).supports_vision
                    else "unsupported"
                )
            profile = ModelProfileModel(
                profile_id=profile_id,
                provider_id=provider_id,
                upstream_model_id=upstream_model_id,
                label=label_text,
                family=family,
                protocol_override=protocol,
                context_tokens=context_tokens,
                max_output_tokens=max_output_tokens,
                thinking_modes_json=json.dumps(modes, separators=(",", ":")),
                default_thinking=default,
                capabilities_json=json.dumps(
                    self._capabilities(raw_caps), separators=(",", ":")
                ),
                enabled=False,
                access_scope=resolved_scope,
                is_default=False,
                sort_order=self._sort_order(data.get("sort_order", 100)),
                verification_json=json.dumps(verification, ensure_ascii=False, separators=(",", ":")),
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
        allowed = {
            "upstream_model_id",
            "label",
            "family",
            "protocol_override",
            "context_tokens",
            "max_output_tokens",
            "thinking_modes",
            "default_thinking",
            "enabled",
            "sort_order",
            "access_scope",
            "capabilities",
            "is_default",
        }
        if set(data) - allowed:
            raise AdminServiceError("model_update_field_invalid")

        with self.database.session() as session:
            self._require_admin(session, actor_id)
            profile = session.get(ModelProfileModel, profile_id)
            if profile is None or profile.archived_at is not None:
                raise AdminServiceError("profile_not_found")
            provider = session.get(ModelProviderModel, profile.provider_id)
            if provider is None or provider.archived_at is not None:
                raise AdminServiceError("provider_not_found")
            provider_revision = self._provider_revision(session, profile.provider_id)

            routing_changed = False
            if "upstream_model_id" in data:
                next_upstream = self._text(
                    data["upstream_model_id"], "upstream_model_required", 200
                )
                if next_upstream != profile.upstream_model_id:
                    routing_changed = True
                profile.upstream_model_id = next_upstream
            if "label" in data:
                profile.label = self._text(data["label"], "model_label_required", 160)
            if "family" in data:
                if data["family"] not in _FAMILIES:
                    raise AdminServiceError("model_family_invalid")
                profile.family = data["family"]
            if "protocol_override" in data:
                if data["protocol_override"] is not None and data["protocol_override"] not in _PROTOCOLS:
                    raise AdminServiceError("model_protocol_invalid")
                if data["protocol_override"] != profile.protocol_override:
                    routing_changed = True
                profile.protocol_override = data["protocol_override"]
            if "context_tokens" in data:
                profile.context_tokens = self._positive_int(
                    data["context_tokens"], "context_tokens_invalid"
                )
            if "max_output_tokens" in data:
                profile.max_output_tokens = self._positive_int(
                    data["max_output_tokens"], "max_output_tokens_invalid"
                )

            modes = (
                self._modes(data["thinking_modes"])
                if "thinking_modes" in data
                else json.loads(profile.thinking_modes_json)
            )
            if "default_thinking" in data:
                if data["default_thinking"] not in modes:
                    raise AdminServiceError("thinking_mode_invalid")
                profile.default_thinking = data["default_thinking"]
            elif profile.default_thinking not in modes:
                profile.default_thinking = modes[0]
            profile.thinking_modes_json = json.dumps(modes, separators=(",", ":"))

            if "sort_order" in data:
                profile.sort_order = self._sort_order(data["sort_order"])
            if "access_scope" in data:
                scope = str(data["access_scope"])
                if scope not in _ACCESS_SCOPES:
                    raise AdminServiceError("access_scope_invalid")
                profile.access_scope = scope
            if "capabilities" in data:
                profile.capabilities_json = json.dumps(
                    self._capabilities(data["capabilities"]), separators=(",", ":")
                )

            next_revision = profile.current_revision + 1
            verification = _json_dict(profile.verification_json)
            connectivity = verification.get("connectivity")
            verified_now = (
                not routing_changed
                and isinstance(connectivity, dict)
                and connectivity.get("status") == "verified"
                and (
                    connectivity.get("provider_revision_id") in (None, provider_revision.id)
                )
            )

            want_enabled = (
                bool(data["enabled"])
                if "enabled" in data
                else (False if routing_changed else profile.enabled)
            )
            if want_enabled and not verified_now:
                raise AdminServiceError("model_connectivity_unverified")

            if routing_changed:
                profile.enabled = False
                profile.is_default = False
                verification = _unverified_payload(
                    profile.verification_json,
                    provider_revision_id=provider_revision.id,
                    profile_revision=next_revision,
                )
            else:
                profile.enabled = want_enabled
                if not profile.enabled:
                    profile.is_default = False
                if isinstance(connectivity, dict) and verified_now:
                    connectivity["profile_revision"] = next_revision

            if "is_default" in data:
                want_default = bool(data["is_default"])
                if want_default:
                    if not profile.enabled:
                        raise AdminServiceError(
                            "model_connectivity_unverified"
                            if not verified_now
                            else "model_must_be_enabled_for_default"
                        )
                    session.execute(
                        update(ModelProfileModel)
                        .where(ModelProfileModel.profile_id != profile_id)
                        .values(is_default=False)
                    )
                    profile.is_default = True
                else:
                    profile.is_default = False

            profile.verification_json = json.dumps(
                verification, ensure_ascii=False, separators=(",", ":")
            )
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
                details={
                    "revision": next_revision,
                    "enabled": profile.enabled,
                    "is_default": profile.is_default,
                },
            )
            session.commit()
            return self._profile_view(profile, provider)

    def archive_provider(self, *, actor_id: str, provider_id: str) -> dict:
        with self.database.session() as session:
            self._require_admin(session, actor_id)
            provider = session.get(ModelProviderModel, provider_id)
            if provider is None or provider.archived_at is not None:
                raise AdminServiceError("provider_not_found")
            now = _now()
            provider.archived_at = now
            provider.enabled = False
            provider.updated_at = now
            profiles = session.scalars(
                select(ModelProfileModel).where(
                    ModelProfileModel.provider_id == provider_id,
                    ModelProfileModel.archived_at.is_(None),
                )
            ).all()
            for profile in profiles:
                profile.archived_at = now
                profile.enabled = False
                profile.is_default = False
                profile.updated_at = now
            AdminService._add_audit(
                session,
                actor_id=actor_id,
                target_account_id=None,
                action="model.provider_archived",
                resource_type="model_provider",
                resource_id=provider_id,
                details={"archived_models": len(profiles)},
            )
            session.commit()
            return {"archived": True, "provider_id": provider_id}

    def archive_profile(self, *, actor_id: str, profile_id: str) -> dict:
        with self.database.session() as session:
            self._require_admin(session, actor_id)
            profile = session.get(ModelProfileModel, profile_id)
            if profile is None or profile.archived_at is not None:
                raise AdminServiceError("profile_not_found")
            now = _now()
            profile.archived_at = now
            profile.enabled = False
            profile.is_default = False
            profile.updated_at = now
            AdminService._add_audit(
                session,
                actor_id=actor_id,
                target_account_id=None,
                action="model.profile_archived",
                resource_type="model_profile",
                resource_id=profile_id,
                details={},
            )
            session.commit()
            return {"archived": True, "profile_id": profile_id}

    def apply_domestic_default_scopes(self, *, actor_id: str, dry_run: bool = False) -> dict:
        """Open Chinese domestic mainstream models to all users and restrict others to group/allowlist."""

        with self.database.session() as session:
            self._require_admin(session, actor_id)
            rows = session.execute(
                select(ModelProfileModel, ModelProviderModel)
                .join(ModelProviderModel, ModelProviderModel.id == ModelProfileModel.provider_id)
                .where(
                    ModelProfileModel.archived_at.is_(None),
                    ModelProviderModel.archived_at.is_(None),
                )
                .order_by(ModelProfileModel.sort_order, ModelProfileModel.created_at)
            ).all()
            changes: list[dict] = []
            projected_scopes: dict[str, str] = {}
            domestic_ids: set[str] = set()
            for profile, provider in rows:
                is_domestic = is_domestic_mainstream_model(
                    provider,
                    profile.upstream_model_id,
                    profile.label,
                    profile.family,
                )
                if is_domestic:
                    domestic_ids.add(profile.profile_id)
                if profile.access_scope == "admin_only":
                    projected_scopes[profile.profile_id] = "admin_only"
                    continue
                target_scope = "all" if is_domestic else "allowlist"
                projected_scopes[profile.profile_id] = target_scope
                if profile.access_scope != target_scope:
                    changes.append(
                        {
                            "profile_id": profile.profile_id,
                            "label": profile.label,
                            "is_domestic": is_domestic,
                            "previous_scope": profile.access_scope,
                            "target_scope": target_scope,
                        }
                    )

            enabled_domestic = [
                profile
                for profile, provider in rows
                if profile.enabled
                and provider.enabled
                and projected_scopes.get(profile.profile_id) == "all"
                and profile.profile_id in domestic_ids
            ]
            current_default = next(
                (profile for profile, _provider in rows if profile.is_default),
                None,
            )
            next_default_profile: ModelProfileModel | None = current_default
            if (
                current_default is None
                or not current_default.enabled
                or projected_scopes.get(current_default.profile_id) != "all"
            ):
                if enabled_domestic:
                    next_default_profile = enabled_domestic[0]
                else:
                    next_default_profile = next(
                        (
                            profile
                            for profile, provider in rows
                            if profile.enabled
                            and provider.enabled
                            and projected_scopes.get(profile.profile_id) == "all"
                        ),
                        None,
                    )
            next_default_id = next_default_profile.profile_id if next_default_profile else None
            next_default_label = next_default_profile.label if next_default_profile else None
            domestic_open_count = sum(
                1 for pid in domestic_ids if projected_scopes.get(pid) == "all"
            )
            if dry_run:
                return {
                    "dry_run": True,
                    "changes": changes,
                    "default_profile_id": next_default_id,
                    "default_profile_label": next_default_label,
                    "domestic_open_count": domestic_open_count,
                }

            now = _now()
            changed_ids = {item["profile_id"] for item in changes}
            for profile, _provider in rows:
                scope_changed = profile.profile_id in changed_ids
                default_changed = (
                    next_default_id is not None
                    and profile.profile_id == next_default_id
                    and not profile.is_default
                ) or (
                    profile.is_default
                    and profile.profile_id != next_default_id
                )
                if not scope_changed and not default_changed:
                    continue
                if scope_changed:
                    profile.access_scope = projected_scopes[profile.profile_id]
                if next_default_id is not None:
                    profile.is_default = profile.profile_id == next_default_id
                elif profile.profile_id in changed_ids and projected_scopes[profile.profile_id] != "all":
                    profile.is_default = False
                next_revision = profile.current_revision + 1
                profile.current_revision = next_revision
                profile.updated_at = now
                provider_revision = self._provider_revision(session, profile.provider_id)
                session.add(
                    ModelProfileRevisionModel(
                        id=f"{profile.profile_id}:r{next_revision}",
                        profile_id=profile.profile_id,
                        provider_revision_id=provider_revision.id,
                        revision=next_revision,
                        facts_json=json.dumps(
                            self._facts(profile), ensure_ascii=False, separators=(",", ":")
                        ),
                        created_at=now,
                    )
                )
            AdminService._add_audit(
                session,
                actor_id=actor_id,
                target_account_id=None,
                action="model.domestic_defaults_applied",
                resource_type="model_directory",
                resource_id=next_default_id,
                details={
                    "changed_count": len(changes),
                    "default_profile_id": next_default_id,
                    "domestic_open_count": domestic_open_count,
                },
            )
            session.commit()
            return {
                "dry_run": False,
                "changes": changes,
                "default_profile_id": next_default_id,
                "default_profile_label": next_default_label,
                "domestic_open_count": domestic_open_count,
            }

    async def discover_models(self, *, actor_id: str, provider_id: str) -> dict:
        with self.database.session() as session:
            self._require_admin(session, actor_id)
            provider = session.get(ModelProviderModel, provider_id)
            if provider is None or provider.archived_at is not None:
                raise AdminServiceError("provider_not_found")
            revision = self._provider_revision(session, provider_id)
            base_url = provider.base_url
            protocol = provider.default_protocol
            auth_style = provider.auth_style
            extra_headers = _json_dict(provider.extra_headers_json)
            api_key = self._resolve_provider_secret(provider, revision)
            existing_upstream = {
                row.upstream_model_id
                for row in session.scalars(
                    select(ModelProfileModel).where(
                        ModelProfileModel.provider_id == provider_id,
                        ModelProfileModel.archived_at.is_(None),
                    )
                ).all()
            }

        try:
            result = await discover_provider_models(
                base_url,
                protocol,
                auth_style,
                api_key,
                extra_headers=extra_headers,
            )
        except EndpointPolicyError as exc:
            raise AdminServiceError(str(exc)) from None
        except ValueError as exc:
            raise AdminServiceError(str(exc)) from None

        return {
            "supported": result.supported,
            "models": [
                {
                    "id": item.id,
                    "family": item.family,
                    "label": item.label,
                    "already_added": item.id in existing_upstream,
                    "context_tokens": item.context_tokens,
                    "max_output_tokens": item.max_output_tokens,
                    "thinking_modes": list(item.thinking_modes),
                    "default_thinking": item.default_thinking,
                    "inferred_matched": item.inferred_matched,
                    "inferred_source": item.inferred_source,
                }
                for item in result.models
            ],
            "notice": result.notice,
        }

    def infer_model(self, *, actor_id: str, model_id: str) -> dict:
        clean_id = self._text(model_id, "upstream_model_required", 200)
        with self.database.session() as session:
            self._require_admin(session, actor_id)
        inferred = infer_model_defaults(clean_id)
        return {
            "model_id": clean_id,
            "family": model_family(clean_id),
            **inferred.to_dict(),
        }

    async def query_provider_balance(self, *, actor_id: str, provider_id: str) -> dict:
        with self.database.session() as session:
            self._require_admin(session, actor_id)
            provider = session.get(ModelProviderModel, provider_id)
            if provider is None or provider.archived_at is not None:
                raise AdminServiceError("provider_not_found")
            revision = self._provider_revision(session, provider_id)
            base_url = provider.base_url
            preset_type = provider.preset_type
            auth_style = provider.auth_style
            extra_headers = _json_dict(provider.extra_headers_json)
            try:
                api_key = self._resolve_provider_secret(provider, revision)
            except AdminServiceError:
                return {
                    "status": "auth_failed",
                    "supported": False,
                    "display": None,
                    "currency": None,
                    "total_balance": None,
                    "granted_balance": None,
                    "topped_up_balance": None,
                    "used_amount": None,
                    "source": None,
                    "notice": "尚未配置可用的 API Key。",
                }

        try:
            result = await probe_provider_balance(
                base_url,
                preset_type,
                auth_style,
                api_key,
                extra_headers=extra_headers,
            )
        except EndpointPolicyError as exc:
            raise AdminServiceError(str(exc)) from None
        except ValueError as exc:
            raise AdminServiceError(str(exc)) from None

        return {
            "status": result.status,
            "supported": result.supported,
            "display": result.display,
            "currency": result.currency,
            "total_balance": result.total_balance,
            "granted_balance": result.granted_balance,
            "topped_up_balance": result.topped_up_balance,
            "used_amount": result.used_amount,
            "source": result.source,
            "notice": result.notice,
        }

    async def test_provider(
        self, *, actor_id: str, provider_id: str, model_id: str | None = None
    ) -> dict:
        with self.database.session() as session:
            self._require_admin(session, actor_id)
            provider = session.get(ModelProviderModel, provider_id)
            if provider is None or provider.archived_at is not None:
                raise AdminServiceError("provider_not_found")
            revision = self._provider_revision(session, provider_id)
            base_url = provider.base_url
            protocol = provider.default_protocol
            auth_style = provider.auth_style
            extra_headers = _json_dict(provider.extra_headers_json)
            api_key = self._resolve_provider_secret(provider, revision)
            target_model = model_id.strip() if isinstance(model_id, str) and model_id.strip() else None
            if target_model is None:
                first_profile = session.scalar(
                    select(ModelProfileModel)
                    .where(
                        ModelProfileModel.provider_id == provider_id,
                        ModelProfileModel.archived_at.is_(None),
                    )
                    .order_by(ModelProfileModel.sort_order, ModelProfileModel.profile_id)
                    .limit(1)
                )
                if first_profile is not None:
                    target_model = first_profile.upstream_model_id
                    if first_profile.protocol_override:
                        protocol = first_profile.protocol_override

        try:
            if target_model is not None:
                probe = await probe_model_endpoint(
                    base_url,
                    protocol,
                    target_model,
                    auth_style,
                    api_key,
                    extra_headers=extra_headers,
                )
                result = {
                    "ok": probe.ok,
                    "connectivity_status": "verified" if probe.ok else "failed",
                    "latency_ms": probe.latency_ms,
                    "http_status": probe.http_status,
                    "error_code": probe.error_code,
                }
            else:
                discovered = await discover_provider_models(
                    base_url,
                    protocol,
                    auth_style,
                    api_key,
                    extra_headers=extra_headers,
                )
                ok = discovered.supported
                result = {
                    "ok": ok,
                    "connectivity_status": "verified" if ok else "failed",
                    "latency_ms": None,
                    "http_status": 200 if ok else None,
                    "error_code": None if ok else (discovered.notice or "provider_probe_requires_model"),
                }
        except EndpointPolicyError as exc:
            result = {
                "ok": False,
                "connectivity_status": "failed",
                "latency_ms": None,
                "http_status": None,
                "error_code": str(exc),
            }
        except ValueError as exc:
            raise AdminServiceError(str(exc)) from None

        with self.database.session() as session:
            self._require_admin(session, actor_id)
            AdminService._add_audit(
                session,
                actor_id=actor_id,
                target_account_id=None,
                action="model.provider_tested",
                resource_type="model_provider",
                resource_id=provider_id,
                outcome="succeeded" if result["ok"] else "failed",
                details={
                    "ok": result["ok"],
                    "http_status": result["http_status"],
                    "error_code": result["error_code"],
                    "model_id": target_model,
                },
            )
            session.commit()
        return result

    async def test_profile(self, *, actor_id: str, profile_id: str) -> dict:
        with self.database.session() as session:
            self._require_admin(session, actor_id)
            profile = session.get(ModelProfileModel, profile_id)
            if profile is None or profile.archived_at is not None:
                raise AdminServiceError("profile_not_found")
            provider = session.get(ModelProviderModel, profile.provider_id)
            if provider is None or provider.archived_at is not None:
                raise AdminServiceError("provider_not_found")
            revision = self._provider_revision(session, provider.id)
            base_url = provider.base_url
            protocol = profile.protocol_override or provider.default_protocol
            auth_style = provider.auth_style
            extra_headers = _json_dict(provider.extra_headers_json)
            api_key = self._resolve_provider_secret(provider, revision)
            upstream_model = profile.upstream_model_id
            provider_revision_id = revision.id
            checked_profile_revision = profile.current_revision

        try:
            probe = await probe_model_endpoint(
                base_url,
                protocol,
                upstream_model,
                auth_style,
                api_key,
                extra_headers=extra_headers,
            )
        except ValueError as exc:
            raise AdminServiceError(str(exc)) from None

        checked_at = _now().isoformat()
        connectivity_status = "verified" if probe.ok else "failed"
        with self.database.session() as session:
            self._require_admin(session, actor_id)
            profile = session.get(ModelProfileModel, profile_id)
            if profile is None or profile.archived_at is not None:
                raise AdminServiceError("profile_not_found")
            latest_provider_rev = self._provider_revision(session, profile.provider_id)
            if latest_provider_rev.id == provider_revision_id:
                verification = _json_dict(profile.verification_json)
                verification["connectivity"] = {
                    "status": connectivity_status,
                    "checked_at": checked_at,
                    "latency_ms": probe.latency_ms,
                    "http_status": probe.http_status,
                    "error_code": probe.error_code,
                    "provider_revision_id": provider_revision_id,
                    "profile_revision": max(checked_profile_revision, profile.current_revision),
                }
                profile.verification_json = json.dumps(
                    verification, ensure_ascii=False, separators=(",", ":")
                )
            AdminService._add_audit(
                session,
                actor_id=actor_id,
                target_account_id=None,
                action="model.profile_tested",
                resource_type="model_profile",
                resource_id=profile_id,
                outcome="succeeded" if probe.ok else "failed",
                details={
                    "ok": probe.ok,
                    "latency_ms": probe.latency_ms,
                    "http_status": probe.http_status,
                    "error_code": probe.error_code,
                },
            )
            session.commit()
        return {
            "ok": probe.ok,
            "connectivity_status": connectivity_status,
            "latency_ms": probe.latency_ms,
            "http_status": probe.http_status,
            "error_code": probe.error_code,
        }

    @staticmethod
    def _resolve_provider_secret(
        provider: ModelProviderModel, revision: ProviderRevisionModel
    ) -> str:
        if provider.auth_style == "none":
            return ""
        if provider.credential_mode == "env_ref":
            value = os.environ.get(provider.credential_ref or "", "").strip()
            if not value:
                raise AdminServiceError("credential_missing")
            return value
        if provider.credential_mode == "encrypted_db":
            try:
                return decrypt_api_key(provider.api_key_encrypted or "").get_secret_value()
            except ValueError as exc:
                raise AdminServiceError(str(exc)) from None
        try:
            if provider.id == "deepseek":
                return load_key(load_settings()).get_secret_value()
            path = revision.credential_ref or os.environ.get("ZHOUXU_RELAY_CREDENTIAL_FILE")
            return load_relay_config(path).key.get_secret_value()
        except Exception as exc:
            raise AdminServiceError("credential_missing") from exc

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
    def _capabilities(value) -> dict:
        # Capabilities are read back on every directory/catalog request, so a
        # wrong type stored here would break those pages for everyone.
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            raise AdminServiceError("capabilities_invalid")
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            raise AdminServiceError("capabilities_invalid") from None
        return value

    @staticmethod
    def _sort_order(value) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not -1_000_000 <= value <= 1_000_000:
            raise AdminServiceError("sort_order_invalid")
        return value

    @staticmethod
    def _modes(value) -> list[str]:
        if not isinstance(value, list) or not value or any(item not in _THINKING for item in value):
            raise AdminServiceError("thinking_modes_invalid")
        return list(dict.fromkeys(value))

    @staticmethod
    def _headers(value) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise AdminServiceError("extra_headers_invalid")
        cleaned: dict[str, str] = {}
        for raw_key, raw_val in value.items():
            if not isinstance(raw_key, str) or not isinstance(raw_val, str):
                raise AdminServiceError("extra_headers_invalid")
            key = raw_key.strip()
            val = raw_val.strip()
            if not key or not val or "\n" in key or "\r" in key or "\n" in val or "\r" in val:
                raise AdminServiceError("extra_headers_invalid")
            if key.lower() in _BLOCKED_HEADERS:
                raise AdminServiceError("model_extra_header_forbidden")
            cleaned[key] = val
        return cleaned

    @staticmethod
    def _env_ref(value) -> str:
        if not isinstance(value, str) or not value.strip():
            raise AdminServiceError("credential_ref_required")
        clean = value.strip()
        if not _ID.fullmatch(clean):
            raise AdminServiceError("credential_ref_invalid")
        if not is_allowed_env_ref(clean):
            raise AdminServiceError("credential_ref_forbidden")
        return clean

    @staticmethod
    def _encrypt_key(value) -> tuple[str, str]:
        if not isinstance(value, str) or not value.strip():
            raise AdminServiceError("api_key_required")
        try:
            return encrypt_api_key(value.strip())
        except ValueError as exc:
            raise AdminServiceError(str(exc)) from None

    @staticmethod
    def _require_admin(session, actor_id: str) -> None:
        from backend.infrastructure.database.models import AccountModel

        actor = session.get(AccountModel, actor_id)
        if actor is None or actor.role != "admin" or actor.status != "active":
            raise AdminServiceError("admin_required")

    @staticmethod
    def _provider_revision(session, provider_id: str) -> ProviderRevisionModel:
        value = session.scalar(
            select(ProviderRevisionModel)
            .where(ProviderRevisionModel.provider_id == provider_id)
            .order_by(ProviderRevisionModel.revision.desc())
            .limit(1)
        )
        if value is None:
            raise AdminServiceError("provider_revision_missing")
        return value

    @staticmethod
    def _endpoint(value) -> str:
        if not isinstance(value, str) or not value.strip():
            raise AdminServiceError("endpoint_required")
        try:
            validate_model_endpoint(value.strip(), allow_local=local_model_hosts_enabled())
        except EndpointPolicyError as exc:
            raise AdminServiceError(str(exc)) from None
        return value.strip().rstrip("/")

    @staticmethod
    def _facts(profile: ModelProfileModel) -> dict:
        return {
            "context_confirmed": False,
            "profile_id": profile.profile_id,
            "provider_id": profile.provider_id,
            "model_id": profile.upstream_model_id,
            "label": profile.label,
            "family": profile.family,
            "protocol_override": profile.protocol_override,
            "context_tokens": profile.context_tokens,
            "max_output_tokens": profile.max_output_tokens,
            "thinking_modes": json.loads(profile.thinking_modes_json),
            "default_thinking": profile.default_thinking,
            "capabilities": json.loads(profile.capabilities_json or "{}"),
            "access_scope": profile.access_scope,
            "source": profile.source,
            "checked_on": profile.checked_on,
        }

    @staticmethod
    def _provider_view(provider: ModelProviderModel, *, revision: int) -> dict:
        configured = False
        if provider.auth_style == "none":
            configured = True
        elif provider.credential_mode == "env_ref":
            configured = bool(os.environ.get(provider.credential_ref or "", "").strip())
        elif provider.credential_mode == "encrypted_db":
            configured = bool(provider.api_key_encrypted and credential_encryption_ready())
        elif provider.credential_mode == "file_ref":
            configured = bool(provider.credential_ref) or provider.id == "deepseek"
        return {
            "id": provider.id,
            "name": provider.name,
            "notes": getattr(provider, "notes", "") or "",
            "preset_type": provider.preset_type,
            "default_protocol": provider.default_protocol,
            "auth_style": provider.auth_style,
            "base_url": provider.base_url,
            "extra_headers": _json_dict(provider.extra_headers_json),
            "credential_mode": provider.credential_mode,
            "credential_ref": provider.credential_ref,
            "api_key_hint": provider.api_key_hint,
            "credential_configured": configured,
            "enabled": provider.enabled,
            "revision": revision,
            "updated_at": provider.updated_at,
        }

    @staticmethod
    def _profile_view(profile: ModelProfileModel, provider: ModelProviderModel) -> dict:
        conn = _connectivity_from(profile.verification_json)
        status = conn.get("status")
        if status not in {"unverified", "verified", "failed"}:
            status = "unverified"
        checked_at = None
        raw_checked = conn.get("checked_at")
        if isinstance(raw_checked, str) and raw_checked:
            try:
                checked_at = datetime.fromisoformat(raw_checked)
            except ValueError:
                checked_at = None
        return {
            "profile_id": profile.profile_id,
            "provider_id": provider.id,
            "upstream_model_id": profile.upstream_model_id,
            "label": profile.label,
            "family": profile.family,
            "protocol_override": profile.protocol_override,
            "protocol": profile.protocol_override or provider.default_protocol,
            "context_tokens": profile.context_tokens,
            "max_output_tokens": profile.max_output_tokens,
            "thinking_modes": json.loads(profile.thinking_modes_json),
            "default_thinking": profile.default_thinking,
            "capabilities": json.loads(profile.capabilities_json or "{}"),
            "enabled": profile.enabled,
            "access_scope": profile.access_scope,
            "is_default": profile.is_default,
            "is_domestic": is_domestic_mainstream_model(
                provider, profile.upstream_model_id, profile.label, profile.family
            ),
            "sort_order": profile.sort_order,
            "source": profile.source,
            "checked_on": profile.checked_on,
            "connectivity_status": status,
            "connectivity_checked_at": checked_at,
            "connectivity_latency_ms": conn.get("latency_ms"),
            "connectivity_error": conn.get("error_code"),
            "revision": profile.current_revision,
            "updated_at": profile.updated_at,
        }


__all__ = ["ModelDirectoryAdminService"]
