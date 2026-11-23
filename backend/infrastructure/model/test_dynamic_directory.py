from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import SecretStr

from backend.config.settings import load_settings
from backend.contracts.model_profiles import ModelCapabilityFlags, ModelProfile
from backend.infrastructure.model.directory import RuntimeModelRevision
from backend.infrastructure.model.router import RoutedModel
from backend.infrastructure.observability.memory import MemoryEventSink


def profile(*, label: str, endpoint: str) -> ModelProfile:
    return ModelProfile(
        profile_id="custom:gpt-preview",
        provider="custom",
        family="gpt",
        label=label,
        model_id="gpt-preview",
        protocol="openai_responses",
        endpoint=endpoint,
        context_tokens=128_000,
        max_generation_tokens=16_000,
        context_confirmed=True,
        thinking_modes=["none", "low"],
        default_thinking_mode="low",
        capabilities=ModelCapabilityFlags(
            tools="unconfirmed", thinking="unconfirmed", streaming="unconfirmed"
        ),
        configured=True,
        available=True,
        availability_note="已配置",
        source="管理员配置",
        checked_on="2026-11-22",
    )


class _Directory:
    def __init__(self):
        self.records = {
            "profile-rev-1": RuntimeModelRevision(
                profile=profile(label="旧配置", endpoint="https://old.example/v1/responses"),
                provider_revision_id="provider-rev-1",
                profile_revision_id="profile-rev-1",
                endpoint="https://old.example/v1/responses",
                auth_style="bearer",
                credential_mode="env_ref",
                credential_ref="OLD_KEY",
            ),
            "profile-rev-2": RuntimeModelRevision(
                profile=profile(label="新配置", endpoint="https://new.example/v1/responses"),
                provider_revision_id="provider-rev-2",
                profile_revision_id="profile-rev-2",
                endpoint="https://new.example/v1/responses",
                auth_style="bearer",
                credential_mode="env_ref",
                credential_ref="NEW_KEY",
            ),
        }

    def list_current(self):
        return [self.records["profile-rev-2"]]

    def resolve(self, profile_id, *, profile_revision_id=None):
        if profile_id != "custom:gpt-preview":
            raise KeyError(profile_id)
        return self.records[profile_revision_id or "profile-rev-2"]

    def credential(self, revision):
        return SecretStr("old-secret" if revision.profile_revision_id.endswith("1") else "new-secret")


class _Adapter:
    def __init__(self, settings, *_):
        self.name = settings.model.name
        self.endpoint = settings.model.endpoint
        self.closed = []
        self.client = SimpleNamespace(headers={"Authorization": "Bearer initial"})

    def close_run(self, run_id):
        self.closed.append(run_id)

    async def __aexit__(self, *_):
        return None


class DynamicDirectoryRoutingTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings().model_copy(deep=True)
        self.settings.model.thinking_mode = "low"
        self.directory = _Directory()

    def test_registry_lists_current_revision_but_can_resolve_historical_revision(self):
        from backend.infrastructure.model.registry import ModelRegistry

        registry = ModelRegistry(self.settings, directory=self.directory)
        self.assertEqual([item.label for item in registry.profiles()], ["新配置"])
        self.assertEqual(
            registry.resolve(
                "custom:gpt-preview", profile_revision_id="profile-rev-1"
            ).profile.label,
            "旧配置",
        )

    def test_router_caches_by_immutable_revision_and_old_run_keeps_old_endpoint(self):
        from backend.infrastructure.model.registry import ModelRegistry

        registry = ModelRegistry(self.settings, directory=self.directory)
        with patch("backend.infrastructure.model.router.DeepSeekModel", _Adapter), patch(
            "backend.infrastructure.model.router.load_key",
            return_value=SecretStr("deepseek"),
        ), patch(
            "backend.infrastructure.model.router.adapter_for", side_effect=_Adapter
        ):
            router = RoutedModel(self.settings, MemoryEventSink(), registry=registry)
            old_snapshot = SimpleNamespace(
                profile_id="custom:gpt-preview",
                profile_revision_id="profile-rev-1",
                provider_revision_id="provider-rev-1",
                provider="custom",
                model_id="gpt-preview",
                protocol="openai_responses",
                thinking_mode="low",
            )
            new_snapshot = SimpleNamespace(
                profile_id="custom:gpt-preview",
                profile_revision_id="profile-rev-2",
                provider_revision_id="provider-rev-2",
                provider="custom",
                model_id="gpt-preview",
                protocol="openai_responses",
                thinking_mode="low",
            )
            router.bind_run("old-run", "custom:gpt-preview", old_snapshot)
            router.bind_run("new-run", "custom:gpt-preview", new_snapshot)
            old_adapter = router._adapter_for("old-run")
            new_adapter = router._adapter_for("new-run")

            self.assertEqual(old_adapter.endpoint, "https://old.example/v1/responses")
            self.assertEqual(new_adapter.endpoint, "https://new.example/v1/responses")
            self.assertIsNot(old_adapter, new_adapter)
            self.assertEqual(len(router._adapters), 2)
            self.assertEqual(old_adapter.client.headers["Authorization"], "Bearer old-secret")
            self.assertEqual(new_adapter.client.headers["Authorization"], "Bearer new-secret")


if __name__ == "__main__":
    unittest.main()
