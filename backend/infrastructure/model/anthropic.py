"""Anthropic Messages wire protocol, including opaque thinking continuation."""

from __future__ import annotations

import json
from copy import deepcopy

from backend.infrastructure.model.decision import DeliveryText
from backend.infrastructure.model.protocols import NativeProtocolModel


class AnthropicMessagesModel(NativeProtocolModel):
    def __init__(self, settings, key, events, *, transport=None):
        super().__init__(settings, key, events, transport=transport)
        del self.client.headers["Authorization"]
        self.client.headers["x-api-key"] = key.get_secret_value()
        self.client.headers["anthropic-version"] = "2023-06-01"

    @staticmethod
    def _content_blocks(content):
        if isinstance(content, str):
            return [{"type": "text", "text": content}] if content else []
        result = []
        for part in content or []:
            if part["type"] == "text":
                result.append({"type": "text", "text": part["text"]})
            elif part["type"] == "image_url":
                url = part["image_url"]["url"]
                if url.startswith("data:"):
                    header, data = url.split(",", 1)
                    media_type, encoding = header[5:].split(";", 1)
                    if encoding != "base64":
                        raise ValueError("image_encoding_invalid")
                    source = {"type": "base64", "media_type": media_type, "data": data}
                else:
                    source = {"type": "url", "url": url}
                result.append({"type": "image", "source": source})
            else:
                raise ValueError("anthropic_content_unsupported")
        return result

    def _anthropic_messages(self, request):
        system = []
        messages = []
        for message in self._messages(request):
            role = message["role"]
            control_results = []
            if role == "system":
                system.extend(self._content_blocks(message["content"]))
                continue
            if role == "tool":
                role = "user"
                blocks = [{"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": message["content"]}]
            else:
                frame = self._frame_for(request, message) if role == "assistant" else None
                if frame is not None:
                    blocks = deepcopy(frame)
                    native_calls = [block for block in blocks if block["type"] == "tool_use"]
                    calls = message.get("tool_calls", [])
                    if native_calls and not calls:
                        controls = {self.settings.model.delivery_tool, self.settings.model.clarification_tool}
                        if any(block["name"] not in controls for block in native_calls):
                            raise ValueError("anthropic_tool_frame_mismatch")
                        control_results = [{"type": "tool_result", "tool_use_id": block["id"], "content": "Application accepted this control result."} for block in native_calls]
                    elif len(native_calls) != len(calls):
                        raise ValueError("anthropic_tool_frame_mismatch")
                    for block, call in zip(native_calls, calls):
                        block["id"] = call["id"]
                else:
                    blocks = self._content_blocks(message.get("content"))
                    blocks.extend({"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": json.loads(call["function"]["arguments"])} for call in message.get("tool_calls", []))
            if not blocks:
                continue
            # Native Messages expects tool results together in the next user turn.
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(blocks)
            else:
                messages.append({"role": role, "content": blocks})
            if control_results:
                messages.append({"role": "user", "content": control_results})
        return system, messages

    def _payload(self, request):
        cfg = self.settings.model
        system, messages = self._anthropic_messages(request)
        packet = {"model": cfg.name, "system": system, "messages": messages, "max_tokens": self.budget.plan(purpose=request.input.purpose).output_reserved}
        tools = self._tools(request.input)
        if tools:
            packet["tools"] = [{"name": tool["function"]["name"], "description": tool["function"].get("description", ""), "input_schema": tool["function"]["parameters"]} for tool in tools]
        if cfg.relay_thinking_style not in {"adaptive", "effort_only"}:
            raise ValueError("anthropic_thinking_style_invalid")
        if cfg.thinking_enabled or cfg.relay_thinking_style == "effort_only":
            if cfg.relay_thinking_style == "adaptive":
                packet["thinking"] = {"type": "adaptive"}
            effort = "low" if request.input.purpose in {"compaction", "review"} else cfg.thinking_mode
            if effort == "none":
                raise ValueError("anthropic_thinking_required")
            packet["output_config"] = {"effort": effort}
        else:
            packet["thinking"] = {"type": "disabled"}
            packet["temperature"] = cfg.temperature
        return packet

    def _response_frame(self, data):
        return deepcopy(data["content"])

    def _normalize_response(self, request, data):
        if data.get("type") == "error":
            raise ValueError("anthropic_stream_error")
        text, calls = [], []
        for block in data["content"]:
            if block["type"] == "text":
                text.append(block["text"])
            elif block["type"] == "tool_use":
                calls.append({"id": block["id"], "type": "function", "function": {"name": block["name"], "arguments": json.dumps(block["input"], ensure_ascii=False)}})
        reason = data.get("stop_reason")
        finish = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length"}.get(reason, reason)
        if calls and reason != "tool_use":
            raise ValueError("anthropic_incomplete_tool_call")
        message = {"content": "".join(text) or None}
        if calls:
            message["tool_calls"] = calls
        usage = data.get("usage", {})
        tokens = sum(usage.get(key, 0) for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        return message, finish, tokens

    def _stream_state(self, request):
        return {"blocks": {}, "arguments": {}, "usage": {}, "stop_reason": None, "stopped": False, "readers": {}, "explicit": self._explicit(request.input.purpose)}

    def _consume_event(self, state, event):
        kind = event["type"]
        if kind == "error":
            raise ValueError("anthropic_stream_error")
        if kind == "message_start":
            state["usage"].update(event["message"].get("usage", {}))
        elif kind == "content_block_start":
            state["blocks"][event["index"]] = deepcopy(event["content_block"])
            block = event["content_block"]
            if block["type"] == "tool_use" and isinstance(block.get("input"), dict):
                # Some gateways put the complete object on the start event
                # and then emit an empty input_json_delta. Keep that object;
                # an empty delta is not malformed JSON.
                state["arguments"][event["index"]] = ""
            if block["type"] == "text" and block.get("text"):
                return [block["text"]]
        elif kind == "content_block_delta":
            index, delta = event["index"], event["delta"]
            block = state["blocks"][index]
            if delta["type"] == "text_delta":
                block["text"] += delta["text"]
                return [delta["text"]]
            if delta["type"] == "thinking_delta":
                block["thinking"] += delta["thinking"]
            elif delta["type"] == "signature_delta":
                block["signature"] = block.get("signature", "") + delta["signature"]
            elif delta["type"] == "input_json_delta":
                partial_json = delta["partial_json"]
                # A few Anthropic-compatible gateways emit an empty delta
                # immediately after starting a control tool.  It carries no
                # JSON and must not be fed to DeliveryText (which correctly
                # rejects an empty/incomplete document).  The final block
                # still gets validated at content_block_stop.
                if not partial_json:
                    return []
                arguments = state["arguments"].get(index, "") + partial_json
                state["arguments"][index] = arguments
                if state["explicit"] and block["name"] == self.settings.model.delivery_tool:
                    visible = state["readers"].setdefault(index, DeliveryText()).push(arguments)
                    return [visible] if visible else []
        elif kind == "content_block_stop":
            index = event["index"]
            if index in state["arguments"]:
                raw = state["arguments"][index].strip()
                if raw:
                    state["blocks"][index]["input"] = json.loads(raw)
                elif not isinstance(state["blocks"][index].get("input"), dict):
                    raise ValueError("anthropic_tool_input_missing")
        elif kind == "message_delta":
            state["stop_reason"] = event["delta"].get("stop_reason")
            state["usage"].update(event.get("usage", {}))
        elif kind == "message_stop":
            state["stopped"] = True
        elif kind != "ping":
            raise ValueError("anthropic_event_unsupported")
        return []

    def _stream_response(self, state):
        if not state["stopped"] or state["stop_reason"] is None:
            raise ValueError("anthropic_stream_incomplete")
        return {"type": "message", "content": [state["blocks"][index] for index in sorted(state["blocks"])], "stop_reason": state["stop_reason"], "usage": state["usage"]}
