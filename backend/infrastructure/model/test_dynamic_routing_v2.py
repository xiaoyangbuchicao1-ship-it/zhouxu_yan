from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from pydantic import SecretStr

from backend.config.settings import AuthSettings, DatabaseSettings, load_settings
from backend.contracts.model_profiles import ModelCapabilityFlags, ModelProfile
from backend.contracts.modules.loop import PerceivedContext, ReasoningInput
from backend.contracts.modules.perception import ContextAssembly, ContextItem
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.database.connection import Database
from backend.infrastructure.model.directory import RuntimeModelRevision
from backend.infrastructure.model.factory import open_model
from backend.infrastructure.model.registry import ModelRegistry, model_family
from backend.infrastructure.model.router import RoutedModel
from backend.infrastructure.observability.memory import MemoryEventSink


class DynamicRoutingV2Tests(unittest.IsolatedAsyncioTestCase):
    def test_model_family_recognizes_qwen_and_kimi(self):
        self.assertEqual(model_family("Qwen/Qwen2.5-72B-Instruct"), "qwen")
        self.assertEqual(model_family("qwen3-235b"), "qwen")
        self.assertEqual(model_family("kimi-k2"), "kimi")
        self.assertEqual(model_family("moonshot-v1-128k"), "kimi")

    async def test_custom_provider_and_thinking_effort_work_without_relay_or_deepseek_hardcode(self):
        settings = load_settings().model_copy(deep=True)
        settings.model.provider = "siliconflow"
        settings.model.protocol = "openai_chat_completions"
        settings.model.name = "Qwen/Qwen2.5-72B-Instruct"
        settings.model.endpoint = "https://api.siliconflow.cn/v1/chat/completions"
        settings.model.allowed_hosts = ["api.siliconflow.cn"]
        settings.model.thinking_mode = "low"
        settings.model.relay_effort_supported = True
        settings.model_capabilities["Qwen/Qwen2.5-72B-Instruct"] = settings.model_capabilities["deepseek-flash"].model_copy(
            update={"provider": "siliconflow", "model_id": "Qwen/Qwen2.5-72B-Instruct"}
        )

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sf-token"}, clear=False):
            async with open_model(settings=settings, events=MemoryEventSink(), role="main") as model:
                self.assertEqual(model.settings.model.provider, "siliconflow")
                packet = model._payload(
                    ModuleRequest(
                        run_id="r1",
                        call_id="c1",
                        input=ReasoningInput(
                            perceived=PerceivedContext(
                                run_id="r1",
                                items=[
                                    ContextItem(
                                        id="current-user",
                                        type="message",
                                        source="user",
                                        role="user",
                                        body={"goal": "你好"},
                                    )
                                ],
                                assembly=ContextAssembly(),
                            ),
                            tools=[],
                        ),
                    )
                )
                self.assertEqual(packet["reasoning_effort"], "low")
                self.assertNotIn("thinking", packet)

    async def test_router_is_lazy_on_startup_and_evicts_unused_stale_revision_adapters(self):
        settings = load_settings().model_copy(deep=True)
        settings.model.thinking_mode = "low"
        rev1 = RuntimeModelRevision(
            profile=ModelProfile(
                profile_id="siliconflow:qwen",
                provider="siliconflow",
                provider_revision_id="siliconflow:r1",
                profile_revision_id="siliconflow:qwen:r1",
                revision=1,
                family="qwen",
                label="Qwen 1",
                model_id="Qwen/Qwen2.5-72B-Instruct",
                protocol="openai_chat_completions",
                endpoint="https://api.siliconflow.cn/v1/chat/completions",
                context_tokens=131072,
                max_generation_tokens=16384,
                thinking_modes=["none", "low"],
                default_thinking_mode="low",
                capabilities=ModelCapabilityFlags(),
                configured=True,
                available=True,
                availability_note="已配置",
                source="admin",
                checked_on="2026-11-22",
            ),
            provider_revision_id="siliconflow:r1",
            profile_revision_id="siliconflow:qwen:r1",
            endpoint="https://api.siliconflow.cn/v1/chat/completions",
            auth_style="bearer",
            credential_mode="env_ref",
            credential_ref="SILICONFLOW_API_KEY",
        )
        rev2 = RuntimeModelRevision(
            profile=rev1.profile.model_copy(
                update={"profile_revision_id": "siliconflow:qwen:r2", "revision": 2, "label": "Qwen 2"}
            ),
            provider_revision_id="siliconflow:r2",
            profile_revision_id="siliconflow:qwen:r2",
            endpoint="https://api.siliconflow.cn/v1/chat/completions",
            auth_style="bearer",
            credential_mode="env_ref",
            credential_ref="SILICONFLOW_API_KEY",
        )

        class _Directory:
            def __init__(self):
                self.current = rev1

            def list_current(self):
                return [self.current]

            def resolve(self, profile_id, *, profile_revision_id=None):
                return rev1 if profile_revision_id == "siliconflow:qwen:r1" else self.current

            def credential(self, revision):
                return SecretStr("key-" + revision.profile_revision_id)

        closed_adapters = []

        class _Adapter:
            def __init__(self, cfg, *_):
                self.name = cfg.model.name
                self.client = SimpleNamespace(headers={})
                self.closed_runs = []

            def close_run(self, run_id):
                self.closed_runs.append(run_id)

            async def aclose(self):
                closed_adapters.append(self.name)

            async def __aexit__(self, *_):
                await self.aclose()

        directory = _Directory()
        registry = ModelRegistry(settings, directory=directory)
        with patch("backend.infrastructure.model.router.adapter_for", side_effect=_Adapter):
            router = RoutedModel(settings, MemoryEventSink(), registry=registry)
            # Must not eagerly construct an unused deepseek-flash adapter.
            self.assertEqual(router._adapters, {})
            router.bind_run("run-1", "siliconflow:qwen")
            first = router._adapter_for("run-1")
            router.close_run("run-1")

            directory.current = rev2
            router.bind_run("run-2", "siliconflow:qwen")
            second = router._adapter_for("run-2")
            self.assertIsNot(first, second)
            self.assertNotIn(("siliconflow:qwen", "siliconflow:qwen:r1"), router._adapters)
            await router.__aexit__(None, None, None)

        # Ensure custom provider runtime_settings preserves custom provider id and enables thinking effort.
        runtime = registry.runtime_settings("siliconflow:qwen")
        self.assertEqual(runtime.model.provider, "siliconflow")
        self.assertTrue(runtime.model.relay_effort_supported)


if __name__ == "__main__":
    unittest.main()
