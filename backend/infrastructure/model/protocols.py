"""Native model transport with one decision contract and private continuations."""

from __future__ import annotations

import json
from copy import deepcopy
from time import perf_counter

import httpx
from pydantic import ValidationError

from backend.contracts.events.runtime import RunEvent
from backend.contracts.modules.loop import DecisionProposal
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.infrastructure.model.decision import DeliveryText
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.model.public_text import PublicText


class NativeProtocolModel(DeepSeekModel):
    """Translate typed context; keep signed native frames out of public text."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._native_continuations: dict[str, dict] = {}

    def export_run_state(self, run_id):
        state = super().export_run_state(run_id)
        native = self._native_continuations.get(run_id)
        if native:
            state.update({
                "provider": self.settings.model.provider,
                "model": self.settings.model.name,
                "state": state.get("state", {"frames": {}, "tools": {}, "decisions": {}}),
                "protocol": self.settings.model.protocol,
                "native": deepcopy(native),
            })
        return state

    def restore_run_state(self, run_id, value):
        super().restore_run_state(run_id, value)
        if not value or "native" not in value:
            return
        native = value["native"]
        if value.get("protocol") != self.settings.model.protocol or not isinstance(native, dict):
            raise PortError(self.settings.error("invalid_context"))
        if set(native) != {"tools", "decisions"} or any(
            not isinstance(native[k], dict)
            or not all(isinstance(name, str) and isinstance(frame, list)
                       and all(isinstance(block, dict) for block in frame)
                       for name, frame in native[k].items())
            for k in ("tools", "decisions")
        ):
            raise PortError(self.settings.error("invalid_context"))
        self._native_continuations[run_id] = deepcopy(native)

    def close_run(self, run_id):
        super().close_run(run_id)
        self._native_continuations.pop(run_id, None)

    async def __aexit__(self, *args):
        self._native_continuations.clear()
        await super().__aexit__(*args)

    def _remember(self, request, message, decision, frame):
        if not frame or request.input.purpose in {"compaction", "review"}:
            return
        state = self._native_continuations.setdefault(request.run_id, {"tools": {}, "decisions": {}})
        for call in message.get("tool_calls", []):
            state["tools"][call["id"]] = deepcopy(frame)
        key = self._decision_key(decision.kind, decision.answer, decision.missing_inputs)
        state["decisions"][key] = deepcopy(frame)

    def _frame_for(self, request, message):
        state = self._native_continuations.get(request.run_id)
        if not state or message.get("role") != "assistant":
            return None
        calls = message.get("tool_calls", [])
        # Cross-Run history is ordinary context, never a provider continuation.
        current_actions = {
            action["action_id"]
            for item in request.input.perceived.items
            if item.type == "tool_call" and item.origin_run_id in {None, request.run_id}
            for action in item.body.get("calls", [])
        }
        for call in calls:
            if call["id"] in current_actions and call["id"] in state["tools"]:
                return deepcopy(state["tools"][call["id"]])
        if not calls and isinstance(message.get("content"), str):
            for item in request.input.perceived.items:
                if item.role != "assistant" or item.origin_run_id not in {None, request.run_id}:
                    continue
                body = item.body
                key = self._decision_key(body.get("kind"), body.get("text"), body.get("missing_inputs"))
                if key in state["decisions"] and body.get("text") and body["text"] in message["content"]:
                    return deepcopy(state["decisions"][key])
        return None

    def _request_endpoint(self, stream=False):
        return self.settings.model.endpoint

    def _request_headers(self):
        return {}

    def _stream_payload(self, request):
        return {**self._payload(request), "stream": True}

    def _stream_state(self, request):
        return {"request": request, "readers": {}}

    def _delivery_delta(self, state, index, name, arguments):
        if name != self.settings.model.delivery_tool or not self._explicit(state["request"].input.purpose):
            return []
        text = state["readers"].setdefault(index, DeliveryText()).push(arguments)
        return [text] if text else []

    def _response_frame(self, data):
        return []

    def _normalize_response(self, request, data):
        raise NotImplementedError

    def _consume_event(self, state, event):
        raise NotImplementedError

    def _stream_response(self, state):
        raise NotImplementedError

    def estimate_context(self, items, tools, *, run_id):
        from backend.contracts.modules.loop import PerceivedContext, ReasoningInput
        from backend.contracts.modules.perception import ContextAssembly
        request = ModuleRequest(
            run_id=run_id, call_id="context-estimate",
            input=ReasoningInput(perceived=PerceivedContext(
                run_id=run_id, items=items, assembly=ContextAssembly()), tools=tools),
        )
        return self.budget.estimate(self._stream_payload(request))

    def _decode_native(self, request, data):
        message, finish, tokens = self._normalize_response(request, data)
        # An empty/failed response is not a completed answer.
        decision = self._decode_message(request, message, finish)
        if decision.kind == "answer" and not (decision.answer or "").strip():
            raise PortError(self.settings.error("model_invalid_response", retryable=True))
        self._remember(request, message, decision, self._response_frame(data))
        return decision, tokens

    def _check_status(self, response):
        if response.status_code == 402:
            raise PortError(self.settings.error("model_quota_exhausted"))
        if response.status_code == 429 or response.status_code >= 500:
            raise PortError(self.settings.error("model_unavailable", retryable=True))
        if not response.is_success:
            raise PortError(self.settings.error("model_rejected"))

    def _event(self, request, status, began, tokens, http_status, error_code=None, first_frame_ms=None):
        self.events.emit(RunEvent(
            run_id=request.run_id, call_id=request.call_id, module="model",
            phase="model", event_type="model", status=status,
            elapsed_seconds=perf_counter() - began, total_tokens=tokens,
            error_code=error_code,
            diagnostics={"protocol": self.settings.model.protocol,
                         "model": self.settings.model.name, "actual_model": self.settings.model.name,
                         "http_status": http_status, "first_frame_ms": first_frame_ms},
        ))

    async def decide(self, request):
        began, status, tokens, http_status, error_code = perf_counter(), "failed", None, None, None
        try:
            response = await self.client.post(
                self._request_endpoint(), json=self._payload(request),
                headers={**self._request_headers(), "x-zhouxu-purpose": request.input.purpose},
            )
            http_status = response.status_code
            self._check_status(response)
            decision, tokens = self._decode_native(request, response.json())
            status = "incomplete" if decision.incomplete else "ok"
            return decision
        except PortError as exc:
            error_code = exc.error.code
            raise
        except httpx.RequestError as exc:
            error_code = "model_unavailable"
            raise PortError(self.settings.error(error_code, retryable=True)) from exc
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            error_code = "model_invalid_response"
            raise PortError(self.settings.error(error_code, retryable=True)) from exc
        finally:
            self._event(request, status, began, tokens, http_status, error_code)

    @staticmethod
    async def _sse(response):
        lines = []
        async for line in response.aiter_lines():
            if line == "":
                if lines:
                    raw = "\n".join(lines)
                    lines = []
                    if raw == "[DONE]":
                        return
                    yield json.loads(raw)
            elif line.startswith("data:"):
                lines.append(line[5:].lstrip())
            elif line.startswith(":") or line.startswith(("event:", "id:", "retry:")):
                continue
            else:
                raise ValueError("invalid_sse_line")
        if lines:
            raw = "\n".join(lines)
            if raw != "[DONE]":
                yield json.loads(raw)

    async def decide_stream(self, request, on_delta):
        began, status, tokens, http_status, error_code = perf_counter(), "failed", None, None, None
        public = PublicText(request.input.perceived.items)
        shown = []
        state = self._stream_state(request)
        first_frame_ms = None
        try:
            async with self.client.stream(
                "POST", self._request_endpoint(stream=True), json=self._stream_payload(request),
                headers={**self._request_headers(), "x-zhouxu-purpose": request.input.purpose},
            ) as response:
                http_status = response.status_code
                self._check_status(response)
                if "application/json" in response.headers.get("content-type", ""):
                    data = json.loads(await response.aread())
                else:
                    async for event in self._sse(response):
                        if first_frame_ms is None:
                            first_frame_ms = round((perf_counter() - began) * 1000, 1)
                        for fragment in self._consume_event(state, event):
                            shown.append(fragment)
                            visible = public.push(fragment)
                            if visible:
                                await on_delta(visible)
                    data = self._stream_response(state)
            decision, tokens = self._decode_native(request, data)
            tail = public.push("", final=True)
            if tail:
                await on_delta(tail)
            status = "incomplete" if decision.incomplete else "ok"
            return decision
        except PortError as exc:
            error_code = exc.error.code
            raise
        except (httpx.RequestError, ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            error_code = "model_unavailable" if isinstance(exc, httpx.RequestError) else "model_invalid_response"
            if shown:
                tail = public.push("", final=True)
                if tail:
                    await on_delta(tail)
                return DecisionProposal(kind="answer", answer=public.render("".join(shown)),
                                        incomplete=True, finish_reason=error_code)
            raise PortError(self.settings.error(error_code, retryable=True)) from exc
        finally:
            self._event(request, status, began, tokens, http_status, error_code, first_frame_ms)


class OpenAIResponsesModel(NativeProtocolModel):
    def _responses_input(self, request):
        result = []
        for message in self._messages(request):
            role = message["role"]
            if role == "system":
                continue
            frame = self._frame_for(request, message)
            if frame:
                result.extend(frame)
                if not message.get("tool_calls"):
                    for item in frame:
                        if item["type"] != "function_call":
                            continue
                        if item["name"] not in {self.settings.model.delivery_tool, self.settings.model.clarification_tool}:
                            raise ValueError("unexpected_replayed_action")
                        result.append({
                            "type": "function_call_output", "call_id": item["call_id"],
                            "output": '{"status":"handled"}',
                        })
                continue
            if role == "tool":
                result.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                               "output": message["content"]})
                continue
            content = message.get("content")
            if content:
                if isinstance(content, list):
                    raise PortError(self.settings.error("model_image_unsupported"))
                result.append({"role": role, "content": [{
                    "type": "output_text" if role == "assistant" else "input_text", "text": content,
                }]})
            for call in message.get("tool_calls", []):
                result.append({"type": "function_call", "call_id": call["id"],
                               "name": call["function"]["name"],
                               "arguments": call["function"]["arguments"]})
        return result

    def _payload(self, request):
        cfg = self.settings.model
        packet = {
            "model": cfg.name, "store": False,
            "instructions": "\n".join(m["content"] for m in self._messages(request) if m["role"] == "system"),
            "input": self._responses_input(request),
            "max_output_tokens": self.budget.plan(purpose=request.input.purpose).output_reserved,
            "include": ["reasoning.encrypted_content"],
        }
        tools = self._tools(request.input)
        if tools:
            packet["tools"] = [{"type": "function", **item["function"]} for item in tools]
        if cfg.relay_effort_supported:
            packet["reasoning"] = {"effort": cfg.thinking_mode}
        return packet

    def _response_frame(self, data):
        return [deepcopy(item) for item in data["output"]
                if item.get("type") in {"reasoning", "function_call", "message"}]

    def _normalize_response(self, request, data):
        text, calls = [], []
        for item in data["output"]:
            if item["type"] == "message":
                for part in item["content"]:
                    if part["type"] == "output_text":
                        text.append(part["text"])
            elif item["type"] == "function_call":
                calls.append({"id": item["call_id"], "type": "function", "function": {
                    "name": item["name"], "arguments": item["arguments"],
                }})
        completion = data["status"]
        if completion == "completed":
            finish = "tool_calls" if calls else "stop"
        elif completion == "incomplete":
            finish = "length" if (data.get("incomplete_details") or {}).get("reason") == "max_output_tokens" else "incomplete"
        elif completion in {"failed", "cancelled"}:
            raise PortError(self.settings.error("model_invalid_response"))
        else:
            raise ValueError("missing_response_completion")
        return {"content": "".join(text) or None, "tool_calls": calls}, finish, (data.get("usage") or {}).get("total_tokens")

    def _stream_state(self, request):
        return {**super()._stream_state(request), "items": {}, "response": None}

    def _consume_event(self, state, event):
        kind = event["type"]
        if kind in {"error", "response.failed"}:
            raise PortError(self.settings.error("model_invalid_response"))
        if kind == "response.output_item.added":
            state["items"][event["output_index"]] = deepcopy(event["item"])
        elif kind == "response.output_text.delta":
            return [event["delta"]]
        elif kind == "response.function_call_arguments.delta":
            item = state["items"][event["output_index"]]
            item["arguments"] = item.get("arguments", "") + event["delta"]
            return self._delivery_delta(state, event["output_index"], item["name"], item["arguments"])
        elif kind == "response.output_item.done":
            state["items"][event["output_index"]] = deepcopy(event["item"])
        elif kind in {"response.completed", "response.incomplete"}:
            state["response"] = event["response"]
        return []

    def _stream_response(self, state):
        if state["response"] is None:
            raise ValueError("missing_response_terminal_event")
        return state["response"]


def adapter_for(settings, key, events):
    protocol = settings.model.protocol
    if protocol == "openai_responses":
        return OpenAIResponsesModel(settings, key, events)
    if protocol == "anthropic_messages":
        from backend.infrastructure.model.anthropic import AnthropicMessagesModel
        return AnthropicMessagesModel(settings, key, events)
    if protocol == "google_generative_language":
        from backend.infrastructure.model.gemini import GeminiNativeModel
        return GeminiNativeModel(settings, key, events)
    if protocol in {"deepseek_chat_completions", "openai_chat_completions"}:
        return DeepSeekModel(settings, key, events)
    raise PortError(settings.error("model_profile_invalid"))
