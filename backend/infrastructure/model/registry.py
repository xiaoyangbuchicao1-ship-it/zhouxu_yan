"""Secret-free provider/profile registry used by the API and model factory."""

from __future__ import annotations

import json
import os
import tomllib
import urllib.parse
from pathlib import Path

from backend.config.settings import BACKEND_ROOT, Settings
from backend.contracts.model_context import ModelCapabilities
from backend.contracts.model_profiles import (
    ModelCapabilityFlags,
    ModelCatalogView,
    ModelFamily,
    ModelFamilyGroup,
    ModelProfile,
)
from backend.contracts.schemas.core import PortError
from backend.infrastructure.model.directory import ModelDirectory, RuntimeModelRevision
from backend.infrastructure.model.relay_config import (
    load_relay_config,
    relay_endpoint_for_protocol,
)

CATALOG_FILE = BACKEND_ROOT / "config" / "model_profiles.toml"
CAPABILITIES_FILE = BACKEND_ROOT / "config" / "relay_capabilities.toml"
VERIFICATION_FILE = BACKEND_ROOT / "config" / "relay_verification.json"
FAMILY_LABELS: dict[ModelFamily, str] = {
    "deepseek": "DeepSeek",
    "gpt": "GPT",
    "claude": "Claude",
    "gemini": "Gemini",
    "glm": "GLM",
    "qwen": "Qwen",
    "kimi": "Kimi",
    "other": "其他",
}

# A relay may expose a model before its exact upstream page is present in the
# checked-in fact table. Keep an explicit, conservative operating budget for
# that case instead of silently falling back to the unrelated 32k default.
# These values are operating ceilings, shared with the public run snapshot.
# context_confirmed distinguishes them from documented model-specific limits.
RELAY_FALLBACK_BUDGETS: dict[str, tuple[int, int]] = {
    "gpt-4o": (128_000, 16_384),
    "gemini-3.7-flash": (1_048_576, 65_536),
    "glm-5-2-260617": (131_072, 32_768),
    "glm-5-3-flash-260828": (131_072, 32_768),
}
RELAY_FAMILY_FALLBACK_BUDGETS: dict[ModelFamily, tuple[int, int]] = {
    "gpt": (1_050_000, 128_000),
    "claude": (1_048_576, 131_072),
    "gemini": (1_048_576, 65_536),
    "glm": (131_072, 32_768),
    "deepseek": (131_072, 32_768),
    "qwen": (131_072, 16_384),
    "kimi": (131_072, 16_384),
    "other": (32_768, 8_192),
}


def model_family(model_id: str) -> ModelFamily:
    name = model_id.lower()
    tail = name.rsplit("/", 1)[-1]
    for target in (name, tail):
        for prefix, family in (
            ("deepseek", "deepseek"),
            ("gpt-", "gpt"),
            ("o1", "gpt"),
            ("o3", "gpt"),
            ("o4", "gpt"),
            ("claude-", "claude"),
            ("gemini-", "gemini"),
            ("glm-", "glm"),
            ("qwen", "qwen"),
            ("kimi", "kimi"),
            ("moonshot-", "kimi"),
        ):
            if target.startswith(prefix):
                return family  # type: ignore[return-value]
    return "other"


def _credential_configured(names: list[str], *, file_path: str | None = None) -> bool:
    if any(os.environ.get(name, "").strip() for name in names):
        return True
    if file_path:
        path = Path(file_path)
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        if path.is_file():
            # Do not return, log, or retain the value.  This only answers the
            # boundary question needed for the public availability badge.
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                key, _, value = line.partition("=")
                if key.strip() in names and value.strip():
                    return True
    return False


def _relay_state() -> tuple[str | None, bool]:
    try:
        config = load_relay_config()
    except ValueError:
        return None, False
    return config.endpoint, True


def relay_chat_endpoint(base: str) -> str:
    """Normalize a relay origin or /v1 origin to chat completions."""
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


class ModelRegistry:
    """Build selectable profiles from checked-in facts and runtime state."""

    def __init__(self, settings: Settings, directory: ModelDirectory | None = None):
        self.settings = settings
        # The explicit SQLite directory is injected by the application after
        # its migration.  Keeping this port optional preserves the checked-in
        # TOML seed for offline tests and migration tooling; runtime callers
        # should pass the database-backed implementation.
        self.directory = directory
        if self.directory is None:
            with CATALOG_FILE.open("rb") as stream:
                self.catalog = tomllib.load(stream)
            with CAPABILITIES_FILE.open("rb") as stream:
                self.relay_capabilities = tomllib.load(stream)["models"]
            self.verification = json.loads(VERIFICATION_FILE.read_text(encoding="utf-8"))
        else:
            self.catalog = {}
            self.relay_capabilities = {}
            self.verification = {}

    def profiles(self) -> list[ModelProfile]:
        if self.directory is not None:
            return [revision.profile for revision in self.directory.list_current()]
        providers = self.catalog["providers"]
        models = self.catalog["models"]
        verified_relay = set(models.get("verified_relay_models", []))
        relay_endpoint, relay_configured = _relay_state()
        deepseek_configured = _credential_configured(
            list(providers["deepseek"]["credential_names"]),
            file_path=str(self.settings.model.credential_file),
        )
        relay_configured = bool(relay_endpoint) and relay_configured
        result = [
            self._deepseek_profile(
                providers["deepseek"],
                model_id=model_id,
                configured=deepseek_configured,
            )
            for model_id in models["deepseek"]
        ]
        result.extend(
            self._relay_profile(
                providers["relay"],
                model_id=model_id,
                endpoint=relay_endpoint,
                configured=relay_configured,
                verified=model_id in verified_relay,
            )
            for model_id in models["relay"]
        )
        return result

    def default_profile_id(self) -> str:
        """Resolve active default profile from directory first, then settings."""

        if self.directory is not None:
            configured = getattr(self.directory, "default_profile_id", None)
            if callable(configured):
                value = configured()
                if isinstance(value, str) and value:
                    return value
        profiles = self.profiles()
        if profiles:
            settings_default = getattr(self.settings.model, "name", "")
            for profile in profiles:
                if profile.profile_id == settings_default:
                    return profile.profile_id
            return profiles[0].profile_id
        return getattr(self.settings.model, "name", "deepseek-flash")

    def catalog_view(
        self,
        profiles: list[ModelProfile] | None = None,
        *,
        default_profile_id: str | None = None,
    ) -> ModelCatalogView:
        profiles = self.profiles() if profiles is None else list(profiles)
        groups = []
        for family in FAMILY_LABELS:
            entries = [profile for profile in profiles if profile.family == family]
            if entries:
                groups.append(
                    ModelFamilyGroup(
                        family=family, label=FAMILY_LABELS[family], models=entries
                    )
                )
        resolved_default = default_profile_id or self.default_profile_id()
        visible_ids = {profile.profile_id for profile in profiles}
        if visible_ids and resolved_default not in visible_ids:
            resolved_default = profiles[0].profile_id
        return ModelCatalogView(
            groups=groups,
            default_profile_id=resolved_default,
        )

    def get(self, profile_id: str) -> ModelProfile:
        for profile in self.profiles():
            if profile.profile_id == profile_id:
                return profile
        raise PortError(self.settings.error("model_profile_invalid"))

    def resolve(
        self, profile_id: str, *, profile_revision_id: str | None = None
    ) -> RuntimeModelRevision:
        """Resolve a current or historical immutable revision.

        The legacy TOML path has no historical revisions, so it creates a
        deterministic wrapper around the profile.  A database directory
        supplies the actual immutable provider/profile revision identities.
        """

        if self.directory is not None:
            try:
                return self.directory.resolve(
                    profile_id, profile_revision_id=profile_revision_id
                )
            except (KeyError, LookupError):
                raise PortError(self.settings.error("model_profile_invalid")) from None
        profile = self.get(profile_id)
        endpoint = profile.endpoint or self.settings.model.endpoint
        provider_revision_id = profile.provider_revision_id or f"{profile.provider}:legacy"
        revision_id = profile.profile_revision_id or f"{profile.profile_id}:legacy"
        auth_style = "bearer"
        if profile.protocol == "anthropic_messages":
            auth_style = "x-api-key"
        elif profile.protocol == "google_generative_language":
            auth_style = "x-goog-api-key"
        return RuntimeModelRevision(
            profile=profile,
            provider_revision_id=provider_revision_id,
            profile_revision_id=revision_id,
            endpoint=endpoint,
            auth_style=auth_style,
            credential_mode="file_ref",
            credential_file=self.settings.model.credential_file,
        )

    def credential(self, revision: RuntimeModelRevision):
        """Resolve a revision's secret through the injected directory.

        The registry never stores or exposes secret text.  Database-backed
        directories implement the actual env-ref/encrypted lookup; the legacy
        seed path keeps using the existing settings loader.
        """

        if self.directory is not None and hasattr(self.directory, "credential"):
            return self.directory.credential(revision)
        if revision.profile.provider == "deepseek":
            from backend.config.settings import load_key

            return load_key(self.settings)
        return load_relay_config(revision.credential_file or self.settings.model.credential_file).key

    def runtime_settings(
        self, profile_id: str, *, profile_revision_id: str | None = None
    ) -> Settings:
        """Return an isolated settings snapshot for one available profile."""
        revision = self.resolve(profile_id, profile_revision_id=profile_revision_id)
        profile = revision.profile
        if not profile.available and profile_revision_id is None:
            raise PortError(self.settings.error("model_profile_invalid"))
        selected = self.settings.model_copy(deep=True)
        model = selected.model
        model.provider = profile.provider
        model.protocol = profile.protocol
        model.name = profile.model_id
        model.endpoint = revision.endpoint or profile.endpoint or ""
        if self.directory is None and profile.provider != "deepseek" and model.endpoint:
            model.endpoint = relay_endpoint_for_protocol(model.endpoint, profile.protocol).format(
                model=profile.model_id
            )
        if model.endpoint:
            hostname = urllib.parse.urlparse(model.endpoint).hostname
            if not hostname:
                raise PortError(self.settings.error("invalid_endpoint"))
            model.allowed_hosts = [hostname]
        elif self.directory is not None or profile.provider != "deepseek":
            raise PortError(self.settings.error("invalid_endpoint"))
        if model.thinking_mode not in profile.thinking_modes:
            raise PortError(self.settings.error("model_profile_invalid"))
        facts = self.relay_capabilities.get(profile.model_id, {})
        if profile.protocol != "deepseek_chat_completions" or profile.provider != "deepseek":
            model.relay_effort_supported = (
                any(mode != "none" for mode in profile.thinking_modes)
                and profile.capabilities.thinking != "unsupported"
            )
            model.relay_thinking_style = facts.get("thinking_style", "adaptive")
            # Tool transport belongs to the adapter; verification is historical
            # evidence and must not disable the shared execution contract.
            model.decision_protocol = "explicit"
        if revision.credential_file:
            model.credential_file = revision.credential_file
        elif profile.provider != "deepseek":
            model.credential_file = os.environ.get("ZHOUXU_RELAY_CREDENTIAL_FILE", model.credential_file)
        if revision.credential_ref:
            model.credential_names = [revision.credential_ref]
        elif profile.provider != "deepseek":
            model.credential_names = ["ZHOUXU_RELAY_API_KEY", "RELAY_API_KEY", "OPENAI_API_KEY"]

        existing_cap = selected.model_capabilities.get(profile.model_id)
        if profile.provider != "deepseek" or existing_cap is None or existing_cap.provider != profile.provider:
            image_transport = "chat_user_data_url" if profile.capabilities.vision == "confirmed" else "none"
            raw_tokens = facts.get("raw_context_tokens")
            selected.model_capabilities[profile.model_id] = ModelCapabilities(
                provider=profile.provider,
                model_id=profile.model_id,
                version_or_alias=profile.model_id,
                raw_context_tokens=raw_tokens,
                input_limit_tokens=facts.get("input_limit_tokens"),
                max_generation_tokens=facts.get("max_generation_tokens") or profile.max_generation_tokens,
                generation_includes_thinking=True,
                image_transport=image_transport,
                image_token_upper_bound=2048 if image_transport != "none" else None,
                counting_method="utf8_upper_bound_estimate",
                source=facts.get("source", [profile.source]),
                checked_on=profile.checked_on,
                conservative_context_tokens=profile.context_tokens,
                conservative_generation_tokens=profile.max_generation_tokens,
                notes="Exact upstream limits when documented; otherwise an explicit family operating ceiling is used until this model is verified. Gateway limits remain a separate deployment constraint.",
            )
        return selected

    def _deepseek_profile(
        self, provider: dict, *, model_id: str, configured: bool
    ) -> ModelProfile:
        cap = self.settings.model_capabilities.get(model_id)
        if cap is None:
            raise PortError(self.settings.error("model_profile_invalid"))
        labels = {
            "deepseek-flash": "DeepSeek Flash",
            "deepseek-v4-pro": "DeepSeek Pro",
        }
        return ModelProfile(
            profile_id=model_id,
            provider="deepseek",
            family="deepseek",
            label=labels.get(model_id, model_id),
            model_id=model_id,
            protocol=provider["protocol"],
            endpoint=self.settings.model.endpoint,
            context_tokens=cap.raw_context_tokens,
            max_generation_tokens=cap.max_generation_tokens,
            context_confirmed=cap.raw_context_tokens is not None,
            thinking_modes=["none", "low", "high", "max"],
            capabilities=ModelCapabilityFlags(
                tools="confirmed",
                thinking="confirmed",
                streaming="confirmed",
                structured_output="confirmed",
                vision="confirmed" if cap.image_transport != "none" else "unsupported",
            ),
            configured=configured,
            available=configured,
            availability_note="已配置" if configured else "未配置 DeepSeek 凭证",
            source=provider["source"],
            checked_on=cap.checked_on,
        )

    def _relay_profile(
        self,
        provider: dict,
        *,
        model_id: str,
        endpoint: str | None,
        configured: bool,
        verified: bool,
    ) -> ModelProfile:
        family = model_family(model_id)
        facts = self.relay_capabilities.get(model_id, {})
        fallback_context, fallback_generation = RELAY_FALLBACK_BUDGETS.get(
            model_id, RELAY_FAMILY_FALLBACK_BUDGETS[family]
        )
        protocol = facts.get("protocol", provider["protocol"])
        probe = self.verification.get(model_id, {})
        # The checked-in catalog list is the durable result of the real
        # low-cost "你好" probe. An optional JSON file can add richer probe
        # facts, but an empty/missing file must not erase that result and make
        # every relay model appear unavailable after a restart.
        protocol_verified = bool(probe.get("hello") and probe.get("protocol") == protocol)
        verified = bool(verified) or protocol_verified
        tools = protocol_verified and probe.get("tools", False)
        # The verification record names the single effort used by the
        # low-cost probe ``thinking_probe_mode``.  The old lookup used
        # ``thinking_mode``, so every relay model was silently downgraded to
        # ``none`` even when its gateway had accepted a real thinking request.
        probe_mode = probe.get("thinking_probe_mode")
        # When upstream documentation is incomplete, use only the effort
        # levels explicitly accepted by the real protocol probes.  A single
        # low-effort probe must not invent the rest, but a recorded per-level
        # probe may safely unlock those exact levels.
        probed_modes = [
            mode for mode in probe.get("thinking_probe_modes", [])
            if mode in self.settings.model.modes and mode != "none"
        ]
        thinking_modes = facts.get("thinking_modes", []) or probed_modes or (
            [probe_mode] if probe_mode in self.settings.model.modes and probe_mode != "none" else []
        )
        thinking = protocol_verified and probe_mode in thinking_modes
        note = (
            "已通过你好连通性检查"
            if configured and verified
            else "已配置，待连通性验证"
            if configured
            else "需要配置模型连接地址和凭证"
        )
        return ModelProfile(
            profile_id=f"relay:{model_id}",
            provider="relay",
            family=family,
            label=model_id,
            model_id=model_id,
            protocol=protocol,
            endpoint=endpoint,
            context_tokens=facts.get("context_tokens", fallback_context),
            max_generation_tokens=facts.get("max_generation_tokens", fallback_generation),
            context_confirmed=facts.get("context_tokens") is not None,
            thinking_modes=thinking_modes if thinking else ["none"],
            default_thinking_mode=facts.get("default_thinking_mode", probe_mode) if thinking else "none",
            capabilities=ModelCapabilityFlags(
                tools="confirmed" if tools else "unconfirmed",
                thinking="confirmed" if thinking else "unconfirmed",
                streaming="confirmed" if verified and probe.get("streaming") else "unconfirmed",
                structured_output="unconfirmed",
                vision="unconfirmed",
            ),
            configured=configured,
            available=configured and verified,
            availability_note=note,
            source="; ".join(facts.get("source", [provider["source"]])),
            checked_on="2026-11-21",
        )


__all__ = ["ModelRegistry", "model_family"]
