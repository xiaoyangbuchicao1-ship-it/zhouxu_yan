"""Golden Responses contracts: real wire envelopes, stream completion and resume."""

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
from backend.infrastructure.model.protocols import OpenAIResponsesModel
from backend.infrastructure.observability.memory import MemoryEventSink


def settings():
    value = load_settings().model_copy(deep=True)
    value.model.provider = "relay"
    value.model.protocol = "openai_responses"
    value.model.name = "gpt-6-astra"
    value.model.endpoint = "https://relay.example/v1/responses"
    value.model.allowed_hosts = ["relay.example"]
    value.model.thinking_mode = "low"
    value.model.relay_effort_supported = True
    value.model.decision_protocol = "explicit"
    value.model_capabilities[value.model.name] = ModelCapabilities(
        provider="relay", model_id=value.model.name, version_or_alias=value.model.name,
        raw_context_tokens=1000000, max_generation_tokens=128000,
        generation_includes_thinking=True, image_transport="none",
        source=["test"], checked_on="2026-11-21", notes="test fixture",
    )
    return value


def request(items=None):
    return ModuleRequest(run_id="run", call_id="call", input=ReasoningInput(
        perceived=PerceivedContext(run_id="run", items=items or [ContextItem(
            id="current-user", type="message", source="user", role="user", body={"goal": "你好"},
        )], assembly=ContextAssembly()),
        tools=[ToolSpec(name="lookup", description="查找", parameters={"type": "object", "properties": {}})],
    ))


def sse(*events):
    return "".join("data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events)


class ResponsesProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_stream_resume_preserves_encrypted_reasoning_and_wire_budget(self):
        captured = []
        output = [
            {"type": "reasoning", "id": "reason-1", "summary": [], "encrypted_content": "opaque-signed-frame"},
            {"type": "function_call", "id": "fc-1", "call_id": "tool-1", "name": "lookup", "arguments": "{}"},
        ]
        final = {"status": "completed", "output": output, "usage": {"total_tokens": 30}}

        def handle(wire):
            captured.append(json.loads(wire.content))
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(
                {"type": "response.output_item.added", "output_index": 1, "item": {**output[1], "arguments": ""}},
                {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": "{}"},
                {"type": "response.output_item.done", "output_index": 1, "item": output[1]},
                {"type": "response.completed", "response": final},
            ))

        events, visible = MemoryEventSink(), []
        model = OpenAIResponsesModel(settings(), SecretStr("test-key"), events, transport=httpx.MockTransport(handle))
        async def on_delta(text):
            visible.append(text)
        async with model:
            result = await model.decide_stream(request(), on_delta)
            self.assertEqual(result.kind, "action")
            self.assertEqual(result.action_requests[0].action_id, "tool-1")
            saved = model.export_run_state("run")
            model.close_run("run")
            model.restore_run_state("run", saved)
            payload = model._payload(request([
                ContextItem(id="calls", type="tool_call", source="reasoning", role="assistant", origin_run_id="run",
                            body={"calls": [{"action_id": "tool-1", "tool_name": "lookup", "arguments": {}}]}),
                ContextItem(id="result", type="tool_result", source="action", role="tool", origin_run_id="run",
                            links=[ContextLink(relation="responds_to", target_id="calls")],
                            body={"action_id": "tool-1", "result": {"text": "资料"}}),
            ]))
        self.assertEqual(payload["input"][:2], output)
        self.assertEqual(payload["input"][2]["call_id"], "tool-1")
        self.assertEqual(len([x for x in payload["input"] if x["type"] == "function_call"]), 1)
        self.assertEqual(captured[0]["reasoning"], {"effort": "low"})
        self.assertIn("max_output_tokens", captured[0])
        self.assertNotIn("max_tokens", captured[0])
        self.assertNotIn("temperature", captured[0])
        self.assertEqual(visible, [])
        self.assertEqual(events.events[-1].diagnostics["http_status"], 200)
        self.assertNotIn("opaque-signed-frame", str(events.events))

    async def test_visible_text_is_streamed_but_missing_terminal_event_is_incomplete(self):
        visible = []
        def handle(wire):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(
                {"type": "response.output_text.delta", "delta": "你好"},
            ))
        async def on_delta(text):
            visible.append(text)
        events = MemoryEventSink()
        async with OpenAIResponsesModel(settings(), SecretStr("test-key"), events, transport=httpx.MockTransport(handle)) as model:
            result = await model.decide_stream(request(), on_delta)
        self.assertEqual(visible, ["你好"])
        self.assertTrue(result.incomplete)
        self.assertEqual(events.events[-1].error_code, "model_invalid_response")

    async def test_rejected_http_and_output_limit_are_not_reported_as_success(self):
        responses = [
            httpx.Response(400, json={"error": {"message": "unsupported request"}}),
            httpx.Response(200, json={"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                                   "output": [{"type": "message", "content": [{"type": "output_text", "text": "未完"}]}]}),
        ]
        events = MemoryEventSink()
        async with OpenAIResponsesModel(settings(), SecretStr("test-key"), events, transport=httpx.MockTransport(lambda _: responses.pop(0))) as model:
            with self.assertRaises(PortError) as caught:
                await model.decide(request())
            self.assertEqual(caught.exception.error.code, "model_rejected")
            self.assertEqual(events.events[-1].diagnostics["http_status"], 400)
            result = await model.decide(request())
        self.assertTrue(result.incomplete)
        self.assertEqual(result.finish_reason, "length")
        self.assertEqual(events.events[-1].status, "incomplete")


if __name__ == "__main__":
    unittest.main()
