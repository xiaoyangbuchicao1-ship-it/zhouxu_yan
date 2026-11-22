"""Native wire/continuation regressions; no paid upstream requests."""

import json
import unittest

import httpx
from pydantic import SecretStr

from backend.config.settings import load_settings
from backend.contracts.model_context import ModelCapabilities
from backend.contracts.modules.loop import PerceivedContext, ReasoningInput, ToolSpec
from backend.contracts.modules.perception import (
    ContextAssembly,
    ContextItem,
    ContextLink,
)
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.infrastructure.model.anthropic import AnthropicMessagesModel
from backend.infrastructure.model.gemini import GeminiNativeModel
from backend.infrastructure.observability.memory import MemoryEventSink


def settings_for(protocol):
    settings = load_settings().model_copy(deep=True)
    settings.model.provider = "relay"
    settings.model.protocol = protocol
    settings.model.name = "native-test"
    settings.model.endpoint = "https://native.example/v1/messages" if protocol == "anthropic_messages" else "https://native.example/v1beta/models/native-test:generateContent"
    settings.model.allowed_hosts = ["native.example"]
    settings.model.thinking_mode = "high"
    settings.model.decision_protocol = "text"
    settings.prompts = {key: "" for key in settings.prompts}
    settings.model_capabilities["native-test"] = ModelCapabilities(
        provider="relay", model_id="native-test", version_or_alias="test",
        raw_context_tokens=262144, max_generation_tokens=65536,
        generation_includes_thinking=True, image_transport="none",
        source=["test"], checked_on="2026-11-21",
        notes="native protocol test",
    )
    return settings


def request_with(items=None):
    return ModuleRequest(run_id="native-run", call_id="native-call", input=ReasoningInput(
        perceived=PerceivedContext(run_id="native-run", assembly=ContextAssembly(), items=items or [ContextItem(id="current-user", type="message", source="user", role="user", body={"goal": "你好"})]),
        tools=[ToolSpec(name="search", description="Search", parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})],
    ))


def tool_results(decision):
    calls = [action.model_dump() for action in decision.action_requests]
    return [
        ContextItem(id="calls", type="tool_call", source="reasoning", role="assistant", origin_run_id="native-run", body={"calls": calls}),
        *[ContextItem(id=f"result-{index}", type="tool_result", source="action", role="tool", origin_run_id="native-run", body={**action, "result": {"text": "找到资料"}}, links=[ContextLink(relation="responds_to", target_id="calls")]) for index, action in enumerate(calls)],
    ]


class EventStream(httpx.AsyncByteStream):
    def __init__(self, events):
        self.events, self.finished = events, False

    async def __aiter__(self):
        for event in self.events:
            yield ("data: " + json.dumps(event) + "\n\n").encode()
        self.finished = True


class NativeProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_claude_initial_input_survives_empty_delta_and_invalid_json_still_fails(self):
        for initial, fragment in [({}, ""), ({"query": "你好"}, ""), ({}, '{"query":')]:
            with self.subTest(initial=initial, fragment=fragment):
                events = [
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "hello-call", "name": "search", "input": initial}},
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": fragment}},
                    {"type": "content_block_stop", "index": 0},
                    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
                    {"type": "message_stop"},
                ]
                async def delta(text):
                    self.fail("A tool call must not expose answer text")
                async with AnthropicMessagesModel(settings_for("anthropic_messages"), SecretStr("test-key"), MemoryEventSink(), transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=EventStream(events)))) as model:
                    if fragment:
                        with self.assertRaises(PortError):
                            await model.decide_stream(request_with(), delta)
                    else:
                        result = await model.decide_stream(request_with(), delta)
                        self.assertEqual(result.action_requests[0].arguments, initial)
                        self.assertEqual(result.action_requests[0].action_id, "hello-call")

    async def test_claude_delivery_tool_ignores_empty_argument_delta(self):
        settings = settings_for("anthropic_messages")
        settings.model.decision_protocol = "explicit"
        name = settings.model.delivery_tool
        events = [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "delivery-call", "name": name, "input": {}}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"text":"你好"}'}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
            {"type": "message_stop"},
        ]
        deltas = []
        async def delta(text):
            deltas.append(text)
        async with AnthropicMessagesModel(
            settings, SecretStr("test-key"), MemoryEventSink(),
            transport=httpx.MockTransport(lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=EventStream(events)
            )),
        ) as model:
            result = await model.decide_stream(request_with(), delta)
        self.assertEqual(result.kind, "answer")
        self.assertEqual(result.answer, "你好")
        self.assertEqual("".join(deltas), "你好")

    async def test_claude_stream_and_resume_preserve_signed_thinking_and_tool_identity(self):
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "private-thinking"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "signed-frame"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "正在查询"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "tool-42", "name": "search", "input": {}}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"query":"你好"}'}},
            {"type": "content_block_stop", "index": 2},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 5}},
            {"type": "message_stop"},
        ]
        stream, seen, deltas = EventStream(events), [], []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=stream)

        async def delta(text):
            self.assertFalse(stream.finished, "visible text must arrive before the whole response finishes")
            deltas.append(text)

        settings = settings_for("anthropic_messages")
        async with AnthropicMessagesModel(settings, SecretStr("test-key"), MemoryEventSink(), transport=httpx.MockTransport(handler)) as model:
            decision = await model.decide_stream(request_with(), delta)
            self.assertEqual(decision.action_requests[0].action_id, "tool-42")
            self.assertEqual("".join(deltas), "正在查询")
            self.assertEqual(seen[0].headers["x-api-key"], "test-key")
            self.assertEqual(seen[0].headers["anthropic-version"], "2023-06-01")
            self.assertNotIn("authorization", seen[0].headers)
            self.assertTrue(json.loads(seen[0].content)["stream"])
            self.assertEqual(json.loads(seen[0].content)["thinking"], {"type": "adaptive"})
            self.assertEqual(json.loads(seen[0].content)["output_config"], {"effort": "high"})
            checkpoint = model.export_run_state("native-run")
        async with AnthropicMessagesModel(settings, SecretStr("test-key"), MemoryEventSink()) as restored:
            restored.restore_run_state("native-run", checkpoint)
            packet = restored._payload(request_with(tool_results(decision)))
            blocks = packet["messages"][0]["content"]
            self.assertEqual(blocks[0], {"type": "thinking", "thinking": "private-thinking", "signature": "signed-frame"})
            self.assertEqual(blocks[2]["id"], "tool-42")
            self.assertEqual(packet["messages"][1]["content"][0]["tool_use_id"], "tool-42")

    async def test_gemini_stream_and_resume_keep_signatures_and_match_function_names(self):
        events = [
            {"candidates": [{"content": {"parts": [{"text": "private-thinking", "thought": True, "thoughtSignature": "thought-sig"}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "正在查询"}]}}]},
            {"candidates": [{"content": {"parts": [{"functionCall": {"id": "upstream-id", "name": "search", "args": {"query": "你好"}}, "thoughtSignature": "call-sig"}, {"functionCall": {"name": "search", "args": {"query": "你好"}}, "thoughtSignature": "second-sig"}]}, "finishReason": "STOP"}], "usageMetadata": {"totalTokenCount": 18}},
        ]
        stream, seen, deltas = EventStream(events), [], []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=stream)

        async def delta(text):
            self.assertFalse(stream.finished)
            deltas.append(text)

        settings = settings_for("google_generative_language")
        async with GeminiNativeModel(settings, SecretStr("test-key"), MemoryEventSink(), transport=httpx.MockTransport(handler)) as model:
            decision = await model.decide_stream(request_with(), delta)
            self.assertEqual("".join(deltas), "正在查询")
            self.assertEqual(seen[0].headers["x-goog-api-key"], "test-key")
            self.assertNotIn("authorization", seen[0].headers)
            self.assertTrue(str(seen[0].url).endswith(":streamGenerateContent?alt=sse"))
            self.assertNotIn("stream", json.loads(seen[0].content))
            self.assertEqual(
                json.loads(seen[0].content)["generationConfig"]["thinkingConfig"],
                {"thinkingLevel": "high"},
            )
            self.assertEqual(len({action.action_id for action in decision.action_requests}), 2)
            checkpoint = model.export_run_state("native-run")
        async with GeminiNativeModel(settings, SecretStr("test-key"), MemoryEventSink()) as restored:
            restored.restore_run_state("native-run", checkpoint)
            packet = restored._payload(request_with(tool_results(decision)))
            parts = packet["contents"][0]["parts"]
            self.assertEqual(parts[0]["thoughtSignature"], "thought-sig")
            self.assertEqual(parts[2]["thoughtSignature"], "call-sig")
            results = packet["contents"][1]["parts"]
            self.assertEqual([part["functionResponse"]["name"] for part in results], ["search", "search"])
            self.assertEqual(results[0]["functionResponse"]["id"], "upstream-id")
            self.assertNotIn("id", results[1]["functionResponse"])

    async def test_native_truncated_or_blocked_responses_never_become_success(self):
        for cls, protocol, events in [
            (AnthropicMessagesModel, "anthropic_messages", [{"type": "message_start", "message": {"usage": {}}}]),
            (GeminiNativeModel, "google_generative_language", [{"promptFeedback": {"blockReason": "SAFETY"}}]),
        ]:
            with self.subTest(protocol=protocol):
                def handler(request):
                    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=EventStream(events))
                async def delta(text):
                    self.fail("An incomplete/blocked response must not expose an answer")
                async with cls(settings_for(protocol), SecretStr("test-key"), MemoryEventSink(), transport=httpx.MockTransport(handler)) as model:
                    with self.assertRaises(PortError):
                        await model.decide_stream(request_with(), delta)

    async def test_native_empty_answer_is_rejected(self):
        for cls, protocol, response in [
            (AnthropicMessagesModel, "anthropic_messages", {"content": [], "stop_reason": "end_turn", "usage": {}}),
            (GeminiNativeModel, "google_generative_language", {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]}),
        ]:
            with self.subTest(protocol=protocol):
                async with cls(settings_for(protocol), SecretStr("test-key"), MemoryEventSink(), transport=httpx.MockTransport(lambda request, payload=response: httpx.Response(200, json=payload))) as model:
                    with self.assertRaises(PortError):
                        await model.decide(request_with())

    async def test_native_length_finish_remains_incomplete(self):
        cases = [
            (AnthropicMessagesModel, "anthropic_messages", {"type": "message", "content": [{"type": "text", "text": "尚未结束"}], "stop_reason": "max_tokens", "usage": {}}),
            (GeminiNativeModel, "google_generative_language", {"candidates": [{"content": {"parts": [{"text": "尚未结束"}]}, "finishReason": "MAX_TOKENS"}]}),
        ]
        for cls, protocol, response in cases:
            with self.subTest(protocol=protocol):
                async with cls(settings_for(protocol), SecretStr("test-key"), MemoryEventSink(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))) as model:
                    decision = await model.decide(request_with())
                    self.assertTrue(decision.incomplete)
                    self.assertEqual(decision.finish_reason, "length")

    async def test_delivered_control_turn_replays_with_receipt_and_original_signature(self):
        for cls, protocol in [(AnthropicMessagesModel, "anthropic_messages"), (GeminiNativeModel, "google_generative_language")]:
            with self.subTest(protocol=protocol):
                settings = settings_for(protocol)
                settings.model.decision_protocol = "explicit"
                name = settings.model.delivery_tool
                if protocol == "anthropic_messages":
                    response = {"content": [{"type": "thinking", "thinking": "private", "signature": "signed"}, {"type": "tool_use", "id": "delivery-id", "name": name, "input": {"text": "你好"}}], "stop_reason": "tool_use"}
                else:
                    response = {"candidates": [{"content": {"parts": [{"functionCall": {"id": "delivery-id", "name": name, "args": {"text": "你好"}}, "thoughtSignature": "signed"}]}, "finishReason": "STOP"}]}
                async with cls(settings, SecretStr("test-key"), MemoryEventSink(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))) as model:
                    decision = await model.decide(request_with())
                    self.assertEqual(decision.answer, "你好")
                    history = ContextItem(id="answer", type="message", source="reasoning", role="assistant", kind="answer", origin_run_id="native-run", body={"kind": "answer", "text": "你好"})
                    packet = model._payload(request_with([history]))
                    if protocol == "anthropic_messages":
                        self.assertEqual(packet["messages"][0]["content"][0]["signature"], "signed")
                        self.assertEqual(packet["messages"][1]["content"][0]["tool_use_id"], "delivery-id")
                    else:
                        self.assertEqual(packet["contents"][0]["parts"][0]["thoughtSignature"], "signed")
                        self.assertEqual(packet["contents"][1]["parts"][0]["functionResponse"]["name"], name)


if __name__ == "__main__":
    unittest.main()
