import unittest

from pydantic import SecretStr

from backend.config.settings import load_settings
from backend.contracts.model_context import ModelCapabilities
from backend.contracts.modules.loop import PerceivedContext, ReasoningInput
from backend.contracts.modules.perception import ContextAssembly, ContextItem
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.observability.memory import MemoryEventSink


class OpenAICompatibleProtocolTests(unittest.TestCase):
    def test_relay_payload_uses_standard_chat_fields_only(self):
        settings = load_settings().model_copy(deep=True)
        settings.model.provider = "relay"
        settings.model.protocol = "openai_chat_completions"
        settings.model.name = "gpt-6.1-sol"
        settings.model.endpoint = "https://relay.example/v1/chat/completions"
        settings.model.allowed_hosts = ["relay.example"]
        settings.model.thinking_mode = "none"
        settings.model.decision_protocol = "text"
        settings.model_capabilities["gpt-6.1-sol"] = ModelCapabilities(
            provider="relay",
            model_id="gpt-6.1-sol",
            version_or_alias="gpt-6.1-sol",
            generation_includes_thinking=True,
            image_transport="none",
            source=["test"],
            checked_on="2026-11-21",
            conservative_context_tokens=32768,
            conservative_generation_tokens=8192,
            notes="test assumption",
        )
        model = DeepSeekModel(settings, SecretStr("test"), MemoryEventSink())
        request = ModuleRequest(
            run_id="run",
            call_id="call",
            input=ReasoningInput(
                perceived=PerceivedContext(
                    run_id="run",
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
        payload = model._payload(request)
        self.assertEqual(payload["model"], "gpt-6.1-sol")
        self.assertNotIn("thinking", payload)
        self.assertNotIn("reasoning_effort", payload)
        self.assertNotIn("tool_choice", payload)

        # Enabling a verified Chat effort must change the actual packet too.
        settings.model.relay_effort_supported = True
        settings.model.thinking_mode = "high"
        payload = model._payload(request)
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertNotIn("thinking", payload)
        self.assertNotIn("reasoning", payload)
        settings.model.thinking_mode = "none"
        payload = model._payload(request)
        self.assertNotIn("reasoning_effort", payload)

    def test_deepseek_thinking_effort_reaches_the_wire(self):
        settings = load_settings().model_copy(deep=True)
        settings.model.name = "deepseek-flash"
        settings.model.thinking_mode = "max"
        model = DeepSeekModel(settings, SecretStr("test"), MemoryEventSink())
        request = ModuleRequest(
            run_id="run",
            call_id="call",
            input=ReasoningInput(
                perceived=PerceivedContext(
                    run_id="run", items=[], assembly=ContextAssembly()
                ),
                tools=[],
            ),
        )
        payload = model._payload(request)
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "max")


if __name__ == "__main__":
    unittest.main()
