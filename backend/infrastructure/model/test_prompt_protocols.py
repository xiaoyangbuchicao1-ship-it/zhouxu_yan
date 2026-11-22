"""Full platform prompts must survive every wire format; no upstream calls."""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import SecretStr

from backend.config.prompt_bundle import load_prompt_bundle
from backend.config.settings import load_settings
from backend.contracts.model_context import ModelCapabilities
from backend.contracts.modules.loop import PerceivedContext, ReasoningInput, ToolSpec
from backend.contracts.modules.perception import ContextAssembly, ContextItem
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.model.anthropic import AnthropicMessagesModel
from backend.infrastructure.model.compaction import COMPACT_PROMPT
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.model.gemini import GeminiNativeModel
from backend.infrastructure.model.protocols import OpenAIResponsesModel
from backend.infrastructure.observability.memory import MemoryEventSink

MAIN_SECTIONS = ("system", "task", "evidence", "delivery", "task_lifecycle")
FROZEN_DATE = datetime(2031, 4, 17, 23, 45, tzinfo=timezone.utc)
ANSWER = "请求格式检查完成。"
BUSINESS_TOOL = "lookup_prompt_evidence"
CANDIDATE_PATH = (Path(__file__).resolve().parents[2] / "config" / "prompts"
                  / "zhouxu-2026-11-22.toml")
ADAPTERS = {
    "openai_chat_completions": DeepSeekModel,
    "openai_responses": OpenAIResponsesModel,
    "anthropic_messages": AnthropicMessagesModel,
    "google_generative_language": GeminiNativeModel,
}
PATHS = {
    "openai_chat_completions": "/v1/chat/completions",
    "openai_responses": "/v1/responses",
    "anthropic_messages": "/v1/messages",
    "google_generative_language": "/v1beta/models/prompt-test:generateContent",
}


def settings_for(protocol, source):
    settings = load_settings().model_copy(deep=True)
    if source == "candidate":
        settings = load_prompt_bundle(CANDIDATE_PATH).apply(settings)
    settings.model.provider = "relay"
    settings.model.protocol = protocol
    settings.model.name = "prompt-test"
    settings.model.endpoint = "https://prompt-protocol.invalid" + PATHS[protocol]
    settings.model.allowed_hosts = ["prompt-protocol.invalid"]
    settings.model.thinking_mode = "none"
    settings.model.decision_protocol = "explicit"
    settings.model.delivery_tool = "deliver_prompt_result"
    settings.model.clarification_tool = "clarify_prompt_request"
    settings.model_capabilities[settings.model.name] = ModelCapabilities(
        provider="relay",
        model_id=settings.model.name,
        version_or_alias="test-fixture",
        raw_context_tokens=262144,
        max_generation_tokens=65536,
        generation_includes_thinking=True,
        image_transport="none",
        source=["no-network-test-fixture"],
        checked_on="2026-11-22",
        notes="Capacity fixture for full, nonempty platform prompt requests.",
    )
    return settings


def request_for(purpose, inventory):
    items = [ContextItem(
        id="current-user", type="message", source="user", role="user",
        body={"goal": "核对材料中的交单义务，引用已取得的原文。"},
    )]
    if inventory:
        items.append(ContextItem(
            id="context-inventory", type="message", source="harness", role="user",
            body={"current_goal": "核对交单义务", "full_text_visible": True,
                  "body_locations": ["已有材料正文"]},
        ))
    return ModuleRequest(
        run_id="prompt-wire-run", call_id="prompt-wire-call",
        input=ReasoningInput(
            purpose=purpose,
            perceived=PerceivedContext(
                run_id="prompt-wire-run", items=items, assembly=ContextAssembly(),
            ),
            tools=[ToolSpec(
                name=BUSINESS_TOOL, description="查询已取得的证据",
                parameters={"type": "object", "properties": {}}, read_only=True,
            )],
        ),
    )


def response_for(protocol):
    if protocol == "openai_chat_completions":
        return {"choices": [{"message": {"role": "assistant", "content": ANSWER},
                             "finish_reason": "stop"}]}
    if protocol == "openai_responses":
        return {"status": "completed", "output": [{
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": ANSWER}],
        }]}
    if protocol == "anthropic_messages":
        return {"content": [{"type": "text", "text": ANSWER}], "stop_reason": "end_turn"}
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": ANSWER}]},
                            "finishReason": "STOP"}]}


def system_text(protocol, packet):
    if protocol == "openai_chat_completions":
        return "\n".join(item["content"] for item in packet["messages"]
                         if item["role"] == "system")
    if protocol == "openai_responses":
        return packet["instructions"]
    if protocol == "anthropic_messages":
        return "\n".join(block["text"] for block in packet["system"])
    return "\n".join(part["text"] for part in packet["systemInstruction"]["parts"])


def tool_names(protocol, packet):
    if protocol == "google_generative_language":
        return [tool["name"] for group in packet.get("tools", [])
                for tool in group["functionDeclarations"]]
    if protocol == "openai_chat_completions":
        return [tool["function"]["name"] for tool in packet.get("tools", [])]
    return [tool["name"] for tool in packet.get("tools", [])]


class FullPromptProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def capture(self, protocol, settings, purpose, inventory):
        captured = []

        def handle(wire):
            self.assertEqual(wire.method, "POST")
            self.assertEqual(wire.url.host, "prompt-protocol.invalid")
            # The budget guard consumes this internal header before forwarding.
            self.assertNotIn("x-zhouxu-purpose", wire.headers)
            captured.append(json.loads(wire.content))
            return httpx.Response(200, json=response_for(protocol))

        with patch("backend.infrastructure.model.deepseek.datetime", wraps=datetime) as clock:
            clock.now.return_value = FROZEN_DATE
            async with ADAPTERS[protocol](
                settings, SecretStr("unused-test-key"), MemoryEventSink(),
                transport=httpx.MockTransport(handle),
            ) as model:
                result = await model.decide(request_for(purpose, inventory))
        self.assertEqual(result.kind, "answer")
        self.assertEqual(result.answer, ANSWER)
        self.assertEqual(len(captured), 1)
        return captured[0]

    async def check_all_protocols(self, purpose="main", inventory=False):
        for source in ("default", "candidate"):
            await self.check_source_protocols(source, purpose, inventory)

    async def check_source_protocols(self, source, purpose, inventory):
        texts = []
        for protocol in ADAPTERS:
            with self.subTest(source=source, protocol=protocol, purpose=purpose,
                              inventory=inventory):
                settings = settings_for(protocol, source)
                prompts = settings.prompts
                required = (*MAIN_SECTIONS, "runtime_date", "decision_protocol",
                            "context_inventory", "compaction", "finalization")
                for key in required:
                    self.assertTrue(prompts[key].strip(), f"Prompt {key} must not be empty")
                packet = await self.capture(protocol, settings, purpose, inventory)
                actual = system_text(protocol, packet).replace("\r\n", "\n")
                if purpose == "compaction":
                    expected = COMPACT_PROMPT + "\n" + prompts["compaction"]
                    self.assertEqual(actual.count(prompts["compaction"].strip()), 1)
                    for key in (*MAIN_SECTIONS, "context_inventory", "finalization"):
                        self.assertNotIn(prompts[key].strip(), actual)
                    self.assertNotIn(FROZEN_DATE.date().isoformat(), actual)
                    self.assertEqual(tool_names(protocol, packet), [])
                else:
                    date_section = prompts["runtime_date"].format(
                        current_date=FROZEN_DATE.date().isoformat(),
                    )
                    expected = "\n".join(prompts[key] for key in MAIN_SECTIONS)
                    expected += "\n" + date_section
                    if inventory:
                        expected += "\n" + prompts["context_inventory"]
                    if purpose == "finalization":
                        expected += "\n" + prompts["finalization"]
                    decision = prompts["decision_protocol"].format(
                        delivery_tool=settings.model.delivery_tool,
                        clarification_tool=settings.model.clarification_tool,
                    )
                    expected += "\n" + decision
                    for key in MAIN_SECTIONS:
                        self.assertEqual(actual.count(prompts[key].strip()), 1, key)
                    self.assertEqual(actual.count(date_section.strip()), 1)
                    self.assertEqual(actual.count(decision.strip()), 1)
                    self.assertEqual(actual.count(prompts["context_inventory"].strip()),
                                     int(inventory))
                    self.assertEqual(actual.count(prompts["finalization"].strip()),
                                     int(purpose == "finalization"))
                    self.assertNotIn(prompts["compaction"].strip(), actual)
                    self.assertNotIn("{current_date}", actual)
                    self.assertNotIn("{delivery_tool}", actual)
                    self.assertNotIn("{clarification_tool}", actual)
                    names = tool_names(protocol, packet)
                    expected_names = {settings.model.delivery_tool, settings.model.clarification_tool}
                    if purpose == "main":
                        expected_names.add(BUSINESS_TOOL)
                    self.assertEqual(set(names), expected_names)
                    self.assertEqual(len(names), len(expected_names))
                self.assertEqual(actual, expected.replace("\r\n", "\n"))
                if protocol == "openai_chat_completions":
                    self.assertEqual(sum(message["role"] == "system"
                                         for message in packet["messages"]), 1)
                texts.append(actual)
        self.assertEqual(len(texts), len(ADAPTERS))
        self.assertTrue(all(text == texts[0] for text in texts[1:]))

    async def test_main_preserves_complete_prompt_once_in_all_wire_formats(self):
        await self.check_all_protocols()

    async def test_inventory_loads_its_section_once_in_all_wire_formats(self):
        await self.check_all_protocols(inventory=True)

    async def test_compaction_uses_only_its_dedicated_prompt_without_tools(self):
        # Even a present inventory must not contaminate the summarizer's rules.
        await self.check_all_protocols(purpose="compaction", inventory=True)

    async def test_finalization_loads_its_section_once_and_excludes_business_tools(self):
        await self.check_all_protocols(purpose="finalization")


if __name__ == "__main__":
    unittest.main()
