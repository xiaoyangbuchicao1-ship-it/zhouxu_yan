"""Gemini GenerateContent protocol with signed parts and SSE streaming."""

from __future__ import annotations

import json
from copy import deepcopy

from backend.infrastructure.model.protocols import NativeProtocolModel


class GeminiNativeModel(NativeProtocolModel):
    def __init__(self, settings, key, events, *, transport=None):
        super().__init__(settings, key, events, transport=transport)
        del self.client.headers["Authorization"]
        self.client.headers["x-goog-api-key"] = key.get_secret_value()

    def _request_endpoint(self, stream=False):
        endpoint = self.settings.model.endpoint
        if not stream:
            return endpoint
        if not endpoint.endswith(":generateContent"):
            raise ValueError("gemini_endpoint_invalid")
        return endpoint.removesuffix(":generateContent") + ":streamGenerateContent?alt=sse"

    def _stream_payload(self, request):
        # Gemini selects SSE through the method/query, not a Chat `stream` field.
        return self._payload(request)

    @staticmethod
    def _content_parts(content):
        if isinstance(content, str):
            return [{"text": content}] if content else []
        result = []
        for part in content or []:
            if part["type"] == "text":
                result.append({"text": part["text"]})
            elif part["type"] == "image_url":
                header, data = part["image_url"]["url"].split(",", 1)
                if not header.startswith("data:") or not header.endswith(";base64"):
                    raise ValueError("gemini_image_encoding_invalid")
                result.append({"inlineData": {"mimeType": header[5:-7], "data": data}})
            else:
                raise ValueError("gemini_content_unsupported")
        return result

    def _gemini_contents(self, request):
        system, contents, call_targets = [], [], {}
        for message in self._messages(request):
            role = message["role"]
            control_results = []
            if role == "system":
                system.extend(self._content_parts(message["content"]))
                continue
            if role == "tool":
                target = call_targets[message["tool_call_id"]]
                response = {"name": target["name"], "response": {"result": message["content"]}}
                if "id" in target:
                    response["id"] = target["id"]
                parts = [{"functionResponse": response}]
                role = "user"
            else:
                frame = self._frame_for(request, message) if role == "assistant" else None
                calls = message.get("tool_calls", [])
                if frame is not None:
                    parts = deepcopy(frame)
                    native_calls = [part["functionCall"] for part in parts if "functionCall" in part]
                    if native_calls and not calls:
                        controls = {self.settings.model.delivery_tool, self.settings.model.clarification_tool}
                        if any(call["name"] not in controls for call in native_calls):
                            raise ValueError("gemini_tool_frame_mismatch")
                        for call in native_calls:
                            result = {"name": call["name"], "response": {"result": "Application accepted this control result."}}
                            if "id" in call:
                                result["id"] = call["id"]
                            control_results.append({"functionResponse": result})
                    elif len(native_calls) != len(calls):
                        raise ValueError("gemini_tool_frame_mismatch")
                    for call, native_call in zip(calls, native_calls):
                        call_targets[call["id"]] = native_call
                else:
                    parts = self._content_parts(message.get("content"))
                    for call in calls:
                        fn = call["function"]
                        native_call = {"name": fn["name"], "args": json.loads(fn["arguments"])}
                        parts.append({"functionCall": native_call})
                        call_targets[call["id"]] = native_call
                role = "model" if role == "assistant" else "user"
            if not parts:
                continue
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": parts})
            if control_results:
                contents.append({"role": "user", "parts": control_results})
        return ({"parts": system} if system else None), contents

    def _payload(self, request):
        cfg = self.settings.model
        system, contents = self._gemini_contents(request)
        packet = {"contents": contents, "generationConfig": {"maxOutputTokens": self.budget.plan(purpose=request.input.purpose).output_reserved}}
        if system:
            packet["systemInstruction"] = system
        if cfg.thinking_enabled:
            packet["generationConfig"]["thinkingConfig"] = {"thinkingLevel": cfg.thinking_mode}
        else:
            packet["generationConfig"]["temperature"] = cfg.temperature
        tools = self._tools(request.input)
        if tools:
            packet["tools"] = [{"functionDeclarations": [{"name": tool["function"]["name"], "description": tool["function"].get("description", ""), "parametersJsonSchema": tool["function"]["parameters"]} for tool in tools]}]
        return packet

    def _response_frame(self, data):
        return deepcopy(data["candidates"][0]["content"]["parts"])

    def _normalize_response(self, request, data):
        if "error" in data or data.get("promptFeedback", {}).get("blockReason"):
            raise ValueError("gemini_response_rejected")
        candidate = data["candidates"][0]
        text, calls = [], []
        for index, part in enumerate(candidate.get("content", {}).get("parts", [])):
            if isinstance(part.get("text"), str) and not part.get("thought"):
                text.append(part["text"])
            if "functionCall" in part:
                call = part["functionCall"]
                calls.append({"id": call.get("id") or f"{request.call_id}:{index}", "type": "function", "function": {"name": call["name"], "arguments": json.dumps(call["args"], ensure_ascii=False)}})
        reason = candidate.get("finishReason")
        if calls and reason != "STOP":
            raise ValueError("gemini_incomplete_tool_call")
        finish = {"STOP": "tool_calls" if calls else "stop", "MAX_TOKENS": "length"}.get(reason, reason)
        message = {"content": "".join(text) or None}
        if calls:
            message["tool_calls"] = calls
        return message, finish, data.get("usageMetadata", {}).get("totalTokenCount")

    def _stream_state(self, request):
        return {"parts": [], "finishReason": None, "usageMetadata": {}, "explicit": self._explicit(request.input.purpose)}

    def _consume_event(self, state, event):
        if "error" in event or event.get("promptFeedback", {}).get("blockReason"):
            raise ValueError("gemini_stream_error")
        if "usageMetadata" in event:
            state["usageMetadata"] = event["usageMetadata"]
        candidates = event.get("candidates", [])
        if len(candidates) > 1:
            raise ValueError("gemini_multiple_candidates")
        if not candidates:
            return []
        candidate = candidates[0]
        if candidate.get("finishReason"):
            state["finishReason"] = candidate["finishReason"]
        visible = []
        for part in candidate.get("content", {}).get("parts", []):
            # Keep every provider part intact so its signature stays attached to
            # the original text/function call when the next turn is assembled.
            state["parts"].append(deepcopy(part))
            if isinstance(part.get("text"), str) and not part.get("thought"):
                visible.append(part["text"])
            call = part.get("functionCall")
            if state["explicit"] and call and call["name"] == self.settings.model.delivery_tool:
                text = call["args"].get("text")
                if isinstance(text, str):
                    visible.append(text)
        return visible

    def _stream_response(self, state):
        if state["finishReason"] is None:
            raise ValueError("gemini_stream_incomplete")
        return {"candidates": [{"content": {"role": "model", "parts": state["parts"]}, "finishReason": state["finishReason"]}], "usageMetadata": state["usageMetadata"]}
