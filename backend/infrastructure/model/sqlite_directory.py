"""SQLite-backed model directory used by both chat and work mode.

The checked-in TOML catalog is only a seed.  Once a database has a directory,
the runtime reads one immutable provider/profile revision from SQLite for each
Run.  This module intentionally keeps credentials out of ORM projections.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from pydantic import SecretStr
from sqlalchemy import select

from backend.config.settings import Settings, load_key
from backend.contracts.model_profiles import ModelCapabilityFlags, ModelProfile
from backend.contracts.schemas.core import PortError
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    ModelProfileModel,
    ModelProfileRevisionModel,
    ModelProviderModel,
    ProviderRevisionModel,
)
from backend.infrastructure.model.directory import RuntimeModelRevision
from backend.infrastructure.model.relay_config import (
    load_relay_config,
    relay_endpoint_for_protocol,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    parsed = json.loads(value) if isinstance(value, str) else value
    return parsed


def _endpoint_for(base_url: str, protocol: str, model_id: str) -> str:
    if not base_url:
        return ""
    # A provider may already store a concrete endpoint.  Otherwise use the
    # shared protocol mapping used by the relay adapter.
    if any(base_url.endswith(suffix) for suffix in ("/chat/completions", "/responses", "/messages")):
        return base_url
    if ":generateContent" in base_url:
        return base_url
    return relay_endpoint_for_protocol(base_url, protocol).format(model=model_id)


class SqliteModelDirectory:
    """Persistent implementation of :class:`ModelDirectory`."""

    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings

    # -- seed -------------------------------------------------------------
    def ensure_seed(self, *, explicit: bool = False) -> None:
        """Seed the checked-in catalog exactly once into an empty directory."""

        with self.database.session() as session:
            if session.scalar(select(ModelProfileModel.profile_id).limit(1)):
                return
            if not explicit and not getattr(self.database, "fresh_bootstrap", False):
                raise RuntimeError("model_catalog_seed_required")
            # Import locally: the legacy registry is the documented one-time
            # TOML seed and itself has no database dependency.
            from backend.infrastructure.model.registry import ModelRegistry

            legacy = ModelRegistry(self.settings)
            profiles = legacy.profiles()
            now = _now()
            providers: dict[str, ModelProviderModel] = {}
            for item in profiles:
                provider_id = "deepseek" if item.provider == "deepseek" else "relay"
                provider = providers.get(provider_id)
                if provider is None:
                    if provider_id == "deepseek":
                        base_url = self.settings.model.endpoint
                        protocol = "deepseek_chat_completions"
                        credential_mode = "file_ref"
                        credential_ref = None
                        preset = "deepseek"
                        name = "DeepSeek"
                    else:
                        endpoint = os.environ.get("ZHOUXU_RELAY_ENDPOINT", "").strip()
                        if not endpoint:
                            try:
                                endpoint = load_relay_config().endpoint
                            except (ValueError, OSError):
                                endpoint = ""
                        base_url = endpoint
                        protocol = "openai_chat_completions"
                        credential_mode = "env_ref"
                        credential_ref = "ZHOUXU_RELAY_API_KEY"
                        preset = "relay"
                        name = "其他模型"
                    provider = ModelProviderModel(
                        id=provider_id,
                        name=name,
                        preset_type=preset,
                        default_protocol=protocol,
                        auth_style="bearer",
                        base_url=base_url,
                        extra_headers_json=None,
                        credential_mode=credential_mode,
                        credential_ref=credential_ref,
                        enabled=True,
                        created_at=now,
                        updated_at=now,
                    )
                    providers[provider_id] = provider
                    session.add(provider)
                    session.add(
                        ProviderRevisionModel(
                            id=f"{provider_id}:r1",
                            provider_id=provider_id,
                            revision=1,
                            default_protocol=protocol,
                            auth_style="bearer",
                            base_url=base_url,
                            credential_mode=credential_mode,
                            credential_ref=credential_ref,
                            config_json=json.dumps({}, separators=(",", ":")),
                            created_at=now,
                        )
                    )
                facts = {
                    "context_confirmed": item.context_confirmed,
                    "profile_id": item.profile_id,
                    "provider_id": provider_id,
                    "model_id": item.model_id,
                    "label": item.label,
                    "family": item.family,
                    "protocol_override": item.protocol,
                    "context_tokens": item.context_tokens or 32768,
                    "max_output_tokens": item.max_generation_tokens or 8192,
                    "thinking_modes": item.thinking_modes,
                    "default_thinking": item.default_thinking_mode,
                    "capabilities": item.capabilities.model_dump(mode="json"),
                    "access_scope": "all",
                    "source": item.source,
                    "checked_on": item.checked_on,
                }
                profile = ModelProfileModel(
                    profile_id=item.profile_id,
                    provider_id=provider_id,
                    upstream_model_id=item.model_id,
                    label=item.label,
                    family=item.family,
                    protocol_override=item.protocol,
                    context_tokens=item.context_tokens or 32768,
                    max_output_tokens=item.max_generation_tokens or 8192,
                    thinking_modes_json=json.dumps(item.thinking_modes, separators=(",", ":")),
                    default_thinking=item.default_thinking_mode,
                    capabilities_json=json.dumps(item.capabilities.model_dump(mode="json"), separators=(",", ":")),
                    enabled=True,
                    access_scope="all",
                    is_default=item.profile_id == "deepseek-flash",
                    sort_order=100,
                    verification_json=None,
                    current_revision=1,
                    source=item.source,
                    checked_on=item.checked_on,
                    created_at=now,
                    updated_at=now,
                )
                session.add(profile)
                session.add(
                    ModelProfileRevisionModel(
                        id=f"{item.profile_id}:r1",
                        profile_id=item.profile_id,
                        provider_revision_id=f"{provider_id}:r1",
                        revision=1,
                        facts_json=json.dumps(facts, ensure_ascii=False, separators=(",", ":")),
                        created_at=now,
                    )
                )
            session.commit()

    # -- directory protocol ---------------------------------------------
    def list_current(self) -> list[RuntimeModelRevision]:
        self.ensure_seed()
        with self.database.session() as session:
            rows = session.scalars(
                select(ModelProfileModel).order_by(ModelProfileModel.sort_order, ModelProfileModel.profile_id)
            ).all()
            return [self._revision_from_row(session, row, historical=False) for row in rows if row.enabled]

    def resolve(self, profile_id: str, *, profile_revision_id: str | None = None) -> RuntimeModelRevision:
        self.ensure_seed()
        with self.database.session() as session:
            row = session.get(ModelProfileModel, profile_id)
            if row is None:
                raise KeyError(profile_id)
            revision = None
            if profile_revision_id:
                revision = session.get(ModelProfileRevisionModel, profile_revision_id)
                if revision is None or revision.profile_id != profile_id:
                    raise KeyError(profile_revision_id)
            return self._revision_from_row(session, row, historical=profile_revision_id is not None, revision=revision)

    def credential(self, revision: RuntimeModelRevision) -> SecretStr:
        if revision.auth_style == "none":
            return SecretStr("")
        if revision.credential_mode == "env_ref":
            name = revision.credential_ref
            value = os.environ.get(name or "", "").strip()
            if not value:
                raise ValueError("credential_missing")
            return SecretStr(value)
        if revision.profile.provider == "deepseek":
            return load_key(self.settings)
        path = revision.credential_file or os.environ.get("ZHOUXU_RELAY_CREDENTIAL_FILE")
        return load_relay_config(path).key

    def default_profile_id(self) -> str:
        self.ensure_seed()
        with self.database.session() as session:
            value = session.scalar(
                select(ModelProfileModel.profile_id)
                .where(ModelProfileModel.enabled.is_(True), ModelProfileModel.is_default.is_(True))
                .limit(1)
            )
            return value or "deepseek-flash"

    # -- conversion ------------------------------------------------------
    def _revision_from_row(self, session, row: ModelProfileModel, *, historical: bool, revision=None) -> RuntimeModelRevision:
        if revision is None:
            revision = session.scalar(
                select(ModelProfileRevisionModel)
                .where(
                    ModelProfileRevisionModel.profile_id == row.profile_id,
                    ModelProfileRevisionModel.revision == row.current_revision,
                )
            )
        if revision is None:
            raise RuntimeError("model_profile_revision_missing")
        provider = session.get(ModelProviderModel, row.provider_id)
        provider_revision = session.get(ProviderRevisionModel, revision.provider_revision_id)
        if provider is None or provider_revision is None:
            raise RuntimeError("model_provider_revision_missing")
        facts = _json(revision.facts_json, {})
        protocol = facts.get("protocol_override") or provider_revision.default_protocol
        capabilities = facts["capabilities"]
        modes = facts["thinking_modes"]
        if not isinstance(modes, list) or not modes:
            raise RuntimeError("model_profile_revision_invalid")
        configured = self._configured(provider, provider_revision, historical=historical)
        current = not historical and row.enabled and provider.enabled
        profile = ModelProfile(
            profile_id=row.profile_id,
            provider="deepseek" if provider.preset_type == "deepseek" else provider.id,
            provider_revision_id=provider_revision.id,
            profile_revision_id=revision.id,
            revision=revision.revision,
            family=facts["family"],
            label=facts["label"],
            model_id=facts["model_id"],
            protocol=protocol,
            endpoint=_endpoint_for(provider_revision.base_url, protocol, facts["model_id"]),
            context_tokens=facts["context_tokens"],
            max_generation_tokens=facts["max_output_tokens"],
            context_confirmed=bool(facts.get("context_confirmed", False)),
            thinking_modes=modes,
            default_thinking_mode=facts["default_thinking"],
            capabilities=ModelCapabilityFlags.model_validate(capabilities),
            configured=configured,
            available=bool((current or historical) and configured),
            availability_note="已配置" if configured else "需要配置模型凭证",
            source=facts["source"],
            checked_on=facts["checked_on"],
        )
        provider_config = _json(provider_revision.config_json, {})
        return RuntimeModelRevision(
            profile=profile,
            provider_revision_id=provider_revision.id,
            profile_revision_id=revision.id,
            endpoint=profile.endpoint or "",
            auth_style=provider_revision.auth_style,
            credential_mode=provider_revision.credential_mode,
            credential_ref=provider_revision.credential_ref,
            credential_file=(
                self.settings.model.credential_file
                if provider.preset_type == "deepseek"
                else provider_revision.credential_ref if provider_revision.credential_mode == "file_ref" else None
            ),
            extra_headers=provider_config.get("extra_headers", {}),
        )

    def _configured(self, provider: ModelProviderModel, revision: ProviderRevisionModel, *, historical: bool) -> bool:
        if revision.auth_style == "none":
            return True
        if not provider.enabled and not historical:
            return False
        if revision.credential_mode == "env_ref":
            return bool(os.environ.get(revision.credential_ref or "", "").strip())
        try:
            if provider.preset_type == "deepseek":
                load_key(self.settings)
            else:
                load_relay_config(
                    revision.credential_ref
                    if revision.credential_mode == "file_ref"
                    else self.settings.model.credential_file
                )
        except (ValueError, OSError, PortError):
            return False
        return True


__all__ = ["SqliteModelDirectory"]
