import json
import unittest
from unittest.mock import patch

import httpx
from pydantic import SecretStr

from backend.config.settings import load_settings
from backend.contracts.modules.loop import PerceivedContext, ReasoningInput, ToolSpec
from backend.contracts.modules.perception import ContextAssembly
from backend.infrastructure.model.budget import ModelBudget
from backend.infrastructure.model.protocols import adapter_for
from backend.infrastructure.model.registry import ModelRegistry, model_family
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.interfaces.api.app import create_app


class ModelRegistryTests(unittest.TestCase):
    def test_relay_models_are_grouped_by_family_without_guessing_capabilities(self):
        registry = ModelRegistry(load_settings())
        registry.verification = {}
        profiles = registry.profiles()
        by_id = {profile.model_id: profile for profile in profiles}

        self.assertEqual(by_id["claude-opus-5"].family, "claude")
        self.assertEqual(by_id["gemini-3.7-flash"].family, "gemini")
        self.assertEqual(by_id["gpt-6.1-sol"].family, "gpt")
        self.assertEqual(by_id["gpt-6.1-sol"].capabilities.tools, "unconfirmed")
        self.assertEqual(by_id["gpt-6.1-sol"].thinking_modes, ["none"])

    def test_cc_switch_verified_gpt_profile_exposes_work_capabilities(self):
        profile = next(
            item
            for item in ModelRegistry(load_settings()).profiles()
            if item.model_id == "gpt-6-astra"
        )
        self.assertEqual(profile.protocol, "openai_responses")
        self.assertEqual(profile.capabilities.tools, "confirmed")
        self.assertEqual(profile.capabilities.streaming, "confirmed")
        self.assertEqual(profile.context_tokens, 1_050_000)

    def test_reprobed_gpt_61_and_gpt_62_profiles_expose_work_capabilities(self):
        with patch("backend.infrastructure.model.registry._relay_state", return_value=("https://models.example/v1", True)):
            profiles = {
                item.model_id: item
                for item in ModelRegistry(load_settings()).profiles()
                if item.model_id in {"gpt-6.1-sol", "gpt-6.2-sol"}
            }
        for model_id in ("gpt-6.1-sol", "gpt-6.2-sol"):
            with self.subTest(model=model_id):
                self.assertTrue(profiles[model_id].available)
                self.assertEqual(profiles[model_id].capabilities.tools, "confirmed")
                self.assertEqual(profiles[model_id].capabilities.streaming, "confirmed")
                self.assertEqual(profiles[model_id].capabilities.thinking, "confirmed")
                self.assertIn("low", profiles[model_id].thinking_modes)

    def test_gpt_62_uses_only_the_effort_levels_with_real_probe_evidence(self):
        profile = next(
            item
            for item in ModelRegistry(load_settings()).profiles()
            if item.model_id == "gpt-6.2-sol"
        )
        self.assertEqual(profile.thinking_modes, ["low", "medium", "high", "xhigh", "max"])

    def test_relay_thinking_probe_enables_documented_effort_levels(self):
        profile = next(
            item
            for item in ModelRegistry(load_settings()).profiles()
            if item.model_id == "gpt-5.6"
        )
        self.assertEqual(profile.capabilities.thinking, "confirmed")
        self.assertEqual(
            profile.thinking_modes,
            ["none", "low", "medium", "high", "xhigh", "max"],
        )

    def test_catalog_contains_deepseek_as_the_safe_default_and_public_shape_is_stable(self):
        catalog = ModelRegistry(load_settings()).catalog_view()
        deepseek = next(group for group in catalog.groups if group.family == "deepseek")

        self.assertEqual(catalog.default_profile_id, "deepseek-flash")
        self.assertEqual(
            [model.profile_id for model in deepseek.models],
            ["deepseek-flash", "deepseek-v4-pro"],
        )
        self.assertEqual(
            next(model for model in deepseek.models if model.profile_id == "deepseek-v4-pro").thinking_modes,
            ["none", "low", "high", "max"],
        )
        self.assertTrue(all(model.endpoint != "" for group in catalog.groups for model in group.models))

    def test_catalog_serialization_never_contains_a_credential(self):
        catalog = ModelRegistry(load_settings()).catalog_view().model_dump_json()
        self.assertNotIn("sk-", catalog)
        self.assertNotIn("api_key", catalog.lower())
        self.assertNotIn("secret", catalog.lower())
        json.loads(catalog)

    def test_unknown_model_family_is_explicitly_other(self):
        self.assertEqual(model_family("text-embedding-3-large"), "other")

    def test_family_budgets_match_public_catalog_and_runtime(self):
        registry = ModelRegistry(load_settings())
        for profile in registry.profiles():
            if profile.provider != "relay":
                continue
            with self.subTest(model=profile.model_id):
                selected = profile.model_copy(update={"available": True, "endpoint": "https://models.example/v1"})
                registry.settings.model.thinking_mode = selected.default_thinking_mode
                with patch.object(registry, "get", return_value=selected):
                    budget = ModelBudget(registry.runtime_settings(profile.profile_id)).plan()
                self.assertEqual(profile.context_tokens, budget.effective_tokens)
                self.assertIsNotNone(profile.max_generation_tokens)
                self.assertLessEqual(budget.warning + budget.output_reserved + budget.safety_tokens, budget.effective_tokens)

    def test_input_only_capacity_is_not_misreported_as_unified_context(self):
        registry = ModelRegistry(load_settings())
        profile = registry.get("relay:gemini-3.8-flash").model_copy(update={"available": True, "endpoint": "https://models.example/v1"})
        registry.settings.model.thinking_mode = profile.default_thinking_mode
        with patch.object(registry, "get", return_value=profile):
            budget = ModelBudget(registry.runtime_settings(profile.profile_id)).plan()
        self.assertIsNone(budget.raw_tokens)
        self.assertEqual(budget.effective_tokens, 1_048_576)
        self.assertLessEqual(budget.hard + budget.output_reserved + budget.safety_tokens, budget.input_limit)


class ModelCatalogApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_available_profiles_use_tools_without_claiming_verified(self):
        registry = ModelRegistry(load_settings())
        registry.verification = {}
        for model_id in ("gpt-6.1-sol", "gpt-4o", "claude-opus-5", "gemini-4-argon"):
            with self.subTest(model=model_id):
                profile = registry.get(f"relay:{model_id}").model_copy(
                    update={"available": True, "endpoint": "https://models.example/v1"}
                )
                registry.settings.model.thinking_mode = profile.default_thinking_mode
                with patch.object(registry, "get", return_value=profile):
                    selected = registry.runtime_settings(profile.profile_id)
                self.assertEqual(selected.model.decision_protocol, "explicit")
                value = ReasoningInput(
                    perceived=PerceivedContext(run_id="test", items=[], assembly=ContextAssembly()),
                    tools=[ToolSpec(name="read", description="读取文件", parameters={"type": "object"}, read_only=True)],
                )
                async with adapter_for(selected, SecretStr("test"), MemoryEventSink()) as model:
                    self.assertIn("read", [item["function"]["name"] for item in model._tools(value)])
                    self.assertTrue(model._explicit(value.purpose))
                self.assertEqual(profile.capabilities.tools, "unconfirmed")

    async def test_catalog_requires_authentication(self):
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/workspace/models")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
