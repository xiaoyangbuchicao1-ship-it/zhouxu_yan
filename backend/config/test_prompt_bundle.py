"""User-supplied prompt fidelity and isolated candidate loading (no model calls)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.config.prompt_bundle import load_prompt_bundle
from backend.config.settings import load_settings
from backend.infrastructure.model.registry import ModelRegistry

CANDIDATE = Path(__file__).parent / "prompts" / "zhouxu-2026-11-22.toml"
# Canonical hash of the 15 values parsed directly from the user's attachment.
USER_CONTENT_SHA256 = "9d1a09fe8a3c57505ab6b734bac77332d8901d8839e40fa9573e01be7adf4d9e"


class PromptBundleTests(unittest.TestCase):
    def test_default_prompts_use_the_approved_release_in_full(self):
        bundle = load_prompt_bundle(CANDIDATE)
        self.assertEqual(load_settings().prompts, dict(bundle.prompts))

    def test_candidate_preserves_all_user_sections_exactly(self):
        bundle = load_prompt_bundle(CANDIDATE)
        self.assertEqual(bundle.content_sha256, USER_CONTENT_SHA256)
        self.assertEqual(len(bundle.prompts), 15)
        self.assertEqual(bundle.status, "candidate")
        self.assertEqual(bundle.version, "2026-11-22.1")
        with self.assertRaises(TypeError):
            bundle.prompts["system"] = "changed"

    def test_explicit_bundle_loading_does_not_mutate_the_caller(self):
        original = load_settings()
        original.prompts["system"] = "Isolated configuration under test"
        before = original.model_dump()
        candidate = load_prompt_bundle(CANDIDATE).apply(original)
        self.assertEqual(original.model_dump(), before)
        self.assertEqual(load_settings().prompts, dict(load_prompt_bundle(CANDIDATE).prompts))
        self.assertNotEqual(candidate.prompts, original.prompts)
        expected = dict(before)
        expected["prompts"] = candidate.prompts
        self.assertEqual(candidate.model_dump(), expected)
        candidate.prompts["system"] = "test-only mutation"
        self.assertNotEqual(candidate.prompts["system"], original.prompts["system"])

    def test_incomplete_or_mistyped_candidate_fails_at_loading_boundary(self):
        raw = CANDIDATE.read_text(encoding="utf-8")
        variants = (
            raw.replace("task_lifecycle =", "unknown_section =", 1),
            raw.replace("{current_date}", "{wrong_date}", 1),
            raw.replace("{clarification_tool}", "{missing_tool}", 1),
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "invalid.toml"
            for invalid in variants:
                with self.subTest(invalid=invalid[:30]):
                    path.write_text(invalid, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_prompt_bundle(path)

    def test_model_selection_keeps_the_same_candidate_for_every_profile(self):
        settings = load_prompt_bundle(CANDIDATE).apply(load_settings())
        registry = ModelRegistry(settings)
        profiles = registry.profiles()
        self.assertTrue({"deepseek", "gpt", "claude", "gemini"}.issubset(
            {profile.family for profile in profiles}
        ))
        for profile in profiles:
            with self.subTest(profile=profile.profile_id):
                # Only exercise routing transformation, with no credential or
                # capability probes and no change to the public catalog.
                selected = profile.model_copy(update={
                    "available": True, "endpoint": "https://prompt.invalid/v1",
                })
                settings.model.thinking_mode = selected.default_thinking_mode
                with patch.object(registry, "get", return_value=selected):
                    actual = registry.runtime_settings(selected.profile_id)
                self.assertEqual(actual.prompts, settings.prompts)
                self.assertIsNot(actual.prompts, settings.prompts)


if __name__ == "__main__":
    unittest.main()
