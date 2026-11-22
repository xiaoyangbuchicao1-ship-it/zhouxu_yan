import unittest

from backend.config.settings import load_settings
from backend.infrastructure.model.registry import ModelRegistry


class RelayCapabilityTests(unittest.TestCase):
    def test_verified_thinking_probe_enables_documented_effort_levels(self):
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

    def test_gpt_astra_exposes_upstream_effort_levels_after_real_probe(self):
        profile = next(
            item
            for item in ModelRegistry(load_settings()).profiles()
            if item.model_id == "gpt-6-astra"
        )
        self.assertEqual(profile.capabilities.thinking, "confirmed")
        self.assertEqual(profile.thinking_modes, ["low", "medium", "high", "xhigh", "max"])

    def test_each_native_protocol_probe_unlocks_only_its_documented_effort_set(self):
        profiles = {item.model_id: item for item in ModelRegistry(load_settings()).profiles()}
        for model_id in ("claude-fable-5-1", "gemini-4-argon", "gpt-6.1-terra"):
            with self.subTest(model=model_id):
                profile = profiles[model_id]
                self.assertEqual(profile.capabilities.thinking, "confirmed")
                self.assertGreater(len(profile.thinking_modes), 1)
        # A successful low probe cannot invent the model's other effort tiers.
        self.assertEqual(profiles["claude-opus-4-6"].thinking_modes, ["low"])
        registry = ModelRegistry(load_settings())
        registry.verification["gpt-6-astra"]["protocol"] = "openai_chat_completions"
        wrong_protocol = next(p for p in registry.profiles() if p.model_id == "gpt-6-astra")
        self.assertEqual(wrong_protocol.capabilities.thinking, "unconfirmed")
        self.assertEqual(wrong_protocol.capabilities.tools, "unconfirmed")


if __name__ == "__main__":
    unittest.main()
