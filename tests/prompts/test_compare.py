"""Prompt A/B preflight must reject identical content before any runtime work."""

import builtins
import importlib.util
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).with_name("compare.py")


def import_compare():
    spec = importlib.util.spec_from_file_location("prompt_compare_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PromptComparisonPreflightTests(unittest.TestCase):
    def test_import_does_not_construct_the_http_application(self):
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "backend.interfaces.api.app":
                self.fail("Prompt preflight must run before importing the HTTP application")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            import_compare()

    def test_identical_prompts_are_rejected_before_credentials_or_model_calls(self):
        compare = import_compare()
        prompts = {"system": "同一套提示词", "task": "同一个任务合同"}
        base = compare.load_settings().model_copy(deep=True)
        base.prompts = prompts
        base.model.endpoint = "https://api.deepseek.com/chat/completions"
        bundle = SimpleNamespace(
            prompts=dict(reversed(list(prompts.items()))),
            content_sha256=compare.prompt_hash(prompts),
            version="test-same-content",
        )
        with tempfile.TemporaryDirectory() as temporary:
            args = Namespace(
                output=Path(temporary), candidate=SCRIPT, scenario="style",
                max_model_calls=1, scenario_timeout=30,
            )
            runtime = Mock(side_effect=AssertionError("HTTP runtime must not start"))
            with (
                patch.object(compare.sys, "platform", "linux"),
                patch.object(compare, "load_settings", return_value=base),
                patch.object(compare, "load_prompt_bundle", return_value=bundle),
                patch.object(Path, "is_file", return_value=True),
                patch.object(compare, "compare_variant", runtime),
                patch("backend.config.settings.load_key") as credentials,
            ):
                operation = compare.main(args)
                try:
                    with self.assertRaisesRegex(compare.FailedCheck, "提示词内容相同"):
                        operation.send(None)
                finally:
                    operation.close()
                runtime.assert_not_called()
                credentials.assert_not_called()
                self.assertFalse((Path(temporary) / "comparison.json").exists())


if __name__ == "__main__":
    unittest.main()
