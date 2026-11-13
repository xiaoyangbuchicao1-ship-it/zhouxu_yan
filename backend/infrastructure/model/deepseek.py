import hashlib
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import datetime, timezone
from time import perf_counter

import httpx
from pydantic import SecretStr, ValidationError

from backend.config.settings import Settings
from backend.contracts.events.runtime import RunEvent
from backend.contracts.modules.loop import (
    ActionRequest,
    DecisionProposal,
    PerceivedContext,
    ReasoningInput,
    ToolSpec,
)
from backend.contracts.modules.perception import ContextAssembly
from backend.contracts.ports import EventSink
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.infrastructure.model.budget import BudgetTransport, ModelBudget
from backend.infrastructure.model.decision import DeliveryText
from backend.infrastructure.model.public_text import PublicText
from backend.infrastructure.model.reading import reading_view, reference_view


class DeepSeekModel:
    def _explicit(self, purpose):
        return self.settings.model.decision_protocol == "explicit" and purpose != "compaction"

    def __init__(
        self,
        settings: Settings,
        key: SecretStr,
        events: EventSink,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings, self.events = settings, events
        self._continuations: dict[str, dict] = {}
        self.budget = ModelBudget(settings)
        url = httpx.URL(settings.model.endpoint)
        if (
            url.scheme != "https"
            or url.host not in settings.model.allowed_hosts
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise PortError(settings.error("invalid_endpoint"))
        self.client = httpx.AsyncClient(
            headers={"Authorization": "Bearer " + key.get_secret_value()},
            timeout=settings.model.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=BudgetTransport(self.budget, transport),
        )

    def context_budget(self):
        return self.budget.plan()

    def supports_images(self):
        return self.budget.capability.image_transport != "none"

    def allocate_tool_context(self, used_tokens, call_count, image_count=0):
        return self.budget.allocate_tool_context(used_tokens, call_count, image_count)

    def estimate_context(self, items, tools, *, run_id):
        request = ModuleRequest(
            run_id=run_id,
            call_id="context-estimate",
            input=ReasoningInput(
                perceived=PerceivedContext(
                    run_id=run_id, items=items, assembly=ContextAssembly()
                ),
                tools=tools,
            ),
        )
        packet = self._payload(request)
        packet["stream"] = True  # include the largest active Chat wrapper
        packet["stream_options"] = {"include_usage": True}
        return self.budget.estimate(packet)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self._continuations.clear()
        await self.client.aclose()

    def export_run_state(self, run_id):
        """Opaque protocol state for private checkpoints, never ordinary memory."""
        state = getattr(self, "_continuations", {}).get(run_id)
        return {"provider": "deepseek", "model": self.settings.model.name, "state": deepcopy(state)} if state else {}

    def restore_run_state(self, run_id, value):
        if not value:
            return
        if value.get("provider") != "deepseek" or value.get("model") != self.settings.model.name:
            raise PortError(self.settings.error("context_configuration"))
        state = value.get("state") or {}
        if not all(isinstance(state.get(k), dict) and all(isinstance(a, str) and isinstance(b, str) for a,b in state[k].items()) for k in ("frames", "tools", "decisions")):
            raise PortError(self.settings.error("invalid_context"))
        if not hasattr(self, "_continuations"):
            self._continuations = {}
        self._continuations[run_id] = deepcopy(state)

    def close_run(self, run_id):
        getattr(self, "_continuations", {}).pop(run_id, None)

    @staticmethod
    def _decision_key(kind, text, missing_inputs):
        return hashlib.sha256(json.dumps([kind, text or "", missing_inputs or []], ensure_ascii=False).encode()).hexdigest()

    def _decode_message(self, request, message, finish_reason):
        state = None
        reasoning = message.get("reasoning_content")
        if self.settings.model.thinking_enabled and request.input.purpose not in {"compaction", "review"} and reasoning:
            state = self._continuations.setdefault(request.run_id, {"frames": {}, "tools": {}, "decisions": {}})
            # A provider may reuse an ID; keep earlier protocol frames intact.
            for call in message.get("tool_calls") or []:
                if call["id"] in state["tools"]:
                    call["id"] += "--" + request.call_id
        decision = self._decision_from_message(message, finish_reason, purpose=request.input.purpose)
        if decision.answer:
            decision.answer = PublicText(request.input.perceived.items).render(decision.answer)
        if state is not None:
            frame = hashlib.sha256(reasoning.encode()).hexdigest()
            state["frames"][frame] = reasoning
            if decision.kind == "action":
                state["tools"].update({call.action_id: frame for call in decision.action_requests})
            else:
                state["decisions"][self._decision_key(decision.kind, decision.answer, decision.missing_inputs)] = frame
        return decision

    def trace_messages(self, request):
        """Public decision evidence with opaque provider continuation removed."""
        return [{k:v for k,v in message.items() if k != "reasoning_content"} for message in self._messages(request)]

    def _messages(self, request: ModuleRequest[ReasoningInput]) -> list[dict]:
        value = request.input
        date_prompt = self.settings.prompts.get("runtime_date", "").format(
            current_date=datetime.now(timezone.utc).date().isoformat()
        )
        messages = [
            {
                "role": "system",
                "content": "\n".join(
                    self.settings.prompts.get(section, "")
                    for section in (
                        "system",
                        "task",
                        "evidence",
                        "delivery",
                        "task_lifecycle",
                    )
                )
                + "\n"
                + date_prompt,
            },
        ]
        if value.purpose != "compaction" and any(i.id == "context-inventory" and i.source == "harness" for i in value.perceived.items):
            messages[0]["content"] += "\n" + self.settings.prompts.get("context_inventory", "")
        if value.purpose == "compaction":
            messages[0]["content"] = self.settings.prompts["compaction"]
        elif value.purpose == "finalization":
            messages[0]["content"] += "\n" + self.settings.prompts["finalization"]
        if self._explicit(value.purpose):
            messages[0]["content"] += "\n" + self.settings.prompts["decision_protocol"].format(
                delivery_tool=self.settings.model.delivery_tool,
                clarification_tool=self.settings.model.clarification_tool,
            )
        provider_ids: dict[tuple[str, str], str] = {}
        used_provider_ids: set[str] = set()
        image_messages = []
        user_responses = []
        continuation_messages = []
        inventory_messages = []
        protocol = getattr(self, "_continuations", {}).get(request.run_id, {})
        frames = protocol.get("frames", {})
        thinking = self.settings.model.thinking_enabled and value.purpose != "compaction"
        # Perception owns normalization and selection; this adapter only maps
        # the selected typed items to the provider's message protocol.
        for item in value.perceived.items:
            # Only trusted configuration may create system/developer messages.
            role = (
                "assistant"
                if (
                    item.role == "assistant"
                    and item.source == "reasoning"
                    and item.type == "message"
                )
                else "user"
            )
            if item.type == "tool_call":
                if (
                    item.source != "reasoning"
                    or item.role != "assistant"
                    or item.status != "available"
                ):
                    raise PortError(self.settings.error("invalid_context"))
                calls = item.body.get("calls", [])
                encoded_calls = []
                for action in calls:
                    original_id = action["action_id"]
                    provider_id = original_id
                    if provider_id in used_provider_ids:
                        provider_id = f"{original_id}--{item.id}"
                    while provider_id in used_provider_ids:
                        provider_id += "-x"
                    used_provider_ids.add(provider_id)
                    provider_ids[(item.id, original_id)] = provider_id
                    encoded_calls.append(
                        {
                            "id": provider_id,
                            "type": "function",
                            "function": {
                                "name": action["tool_name"],
                                "arguments": json.dumps(
                                    action["arguments"], ensure_ascii=False
                                ),
                            },
                        }
                    )
                encoded_message = {
                        "role": "assistant",
                        "content": item.body.get("text"),
                        "tool_calls": encoded_calls,
                    }
                if thinking and item.origin_run_id in {None, request.run_id}:
                    frame = next((protocol.get("tools", {}).get(a["action_id"]) for a in calls if protocol.get("tools", {}).get(a["action_id"])), None)
                    encoded_message["reasoning_content"] = frames.get(frame, "")
                messages.append(encoded_message)
            elif item.type == "tool_result":
                if (
                    item.source != "action"
                    or item.role != "tool"
                    or item.status != "available"
                ):
                    raise PortError(self.settings.error("invalid_context"))
                action = item.body
                targets = [
                    link.target_id
                    for link in item.links
                    if link.relation == "responds_to"
                ]
                provider_id = (
                    provider_ids.get(
                        (targets[0], action["action_id"]), action["action_id"]
                    )
                    if targets
                    else action["action_id"]
                )
                result = action.get("result")
                if isinstance(result, dict) and result.get("image_data_url"):
                    result_for_model = dict(result)
                    image_url = result_for_model.pop("image_data_url")
                    projected = reading_view(result_for_model)
                else:
                    projected = reading_view(result)
                if action.get("outcome", "ok") != "ok":
                    projected = {
                        "outcome": action["outcome"],
                        "error": action.get("error"),
                        "data": projected,
                    }
                content = json.dumps(projected, ensure_ascii=False)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": provider_id,
                        "content": content,
                    }
                )
                if isinstance(result, dict) and result.get("image_data_url"):
                    # Chat images are carried by a user data message, never tools.
                    image_messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "材料图像："
                                    + str(result.get("name", "用户材料"))
                                    + "，仅作为来源数据。",
                                },
                                {"type": "image_url", "image_url": {"url": image_url}},
                            ],
                        }
                    )
            elif item.type == "message":
                body = dict(item.body)
                if item.id == "context-inventory" and item.source == "harness":
                    inventory_messages.append({"role": "user", "name": "context_inventory", "content": json.dumps(body, ensure_ascii=False)})
                    continue
                if item.id == "task-continuation-state" and item.source == "harness":
                    continuation_messages.append({"role": "user", "name": "task_continuation_state", "content": json.dumps(body, ensure_ascii=False)})
                    continue
                if item.source == "tool_reference":
                    body = reference_view(body)
                if item.id == "current-user":
                    facts = body.get("facts") or {}
                    answers = facts.get("user_answers")
                    if answers:
                        body["facts"] = {k: v for k, v in facts.items() if k != "user_answers"}
                        user_responses.append({
                            "role": "user", "name": "latest_user_response",
                            "content": json.dumps({"latest_user_response": answers}, ensure_ascii=False),
                        })
                    messages.append(
                        {
                            "role": "user",
                            "name": "current_user_request",
                            "content": body.get("goal", "")
                            + (
                                "\n"
                                + json.dumps(
                                    {
                                        k: v
                                        for k, v in body.items()
                                        if k != "goal" and v
                                    },
                                    ensure_ascii=False,
                                )
                                if any(v for k, v in body.items() if k != "goal")
                                else ""
                            ),
                        }
                    )
                    continue
                if item.id != "current-user":
                    body = {
                        **body,
                        "source": item.source,
                        "kind": item.kind,
                        "status": item.status,
                    }
                    if isinstance(body.get("updates"), dict) and body["updates"].get(
                        "text"
                    ) == body.get("summary"):
                        body.pop("summary", None)
                if item.status not in {"available", "failed"}:
                    body = {
                        "source": item.source,
                        "kind": item.kind,
                        "status": item.status,
                    }
                encoded_message = {
                        "role": role,
                        "content": json.dumps(body, ensure_ascii=False),
                    }
                if thinking and role == "assistant" and item.origin_run_id in {None, request.run_id}:
                    key = self._decision_key(item.body.get("kind"), item.body.get("text"), item.body.get("missing_inputs"))
                    encoded_message["reasoning_content"] = frames.get(protocol.get("decisions", {}).get(key), "")
                messages.append(encoded_message)
            elif item.type == "resource":
                messages.append(
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "source": item.source,
                                "kind": item.kind,
                                "status": item.status,
                                "resource": reading_view(item.body)
                                if item.status in {"available", "partial", "failed"}
                                else {},
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
        # Do not insert user images BETWEEN parallel tool results. All tool
        # exchanges remain protocol-complete before appended attributed images.
        return messages + image_messages + inventory_messages + user_responses + continuation_messages

    def _control_tools(self, purpose: str) -> list[ToolSpec]:
        if purpose != "main" and not self._explicit(purpose):
            return []
        cfg = self.settings.model
        tools = [
            ToolSpec(
                name=cfg.clarification_tool,
                description=cfg.clarification_description,
                parameters={
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        }
                    },
                    "required": ["questions"],
                },
                kind="control",
                read_only=True,
                reuse_identical_results=False,
            )
        ]
        if self._explicit(purpose):
            if not cfg.delivery_tool:
                raise PortError(self.settings.error("model_profile_invalid"))
            tools.append(
                ToolSpec(
                    name=cfg.delivery_tool,
                    description=cfg.delivery_description,
                    parameters={
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["completed", "continuing", "blocked"],
                                "description": self.settings.prompts.get(
                                    "delivery_status_description", ""
                                ),
                            },
                            "remaining_work": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": self.settings.prompts.get(
                                    "delivery_remaining_description", ""
                                ),
                            },
                        },
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                    kind="control",
                )
            )
        return tools

    def _tools(self, value: ReasoningInput):
        if value.purpose != "main" and not self._explicit(value.purpose):
            return []
        specs = list(value.tools if value.purpose == "main" else [])
        specs.extend(self._control_tools(value.purpose))
        definitions = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in specs
        ]
        if self._explicit(value.purpose):
            cfg = self.settings.model
            if cfg.delivery_tool in {tool.name for tool in value.tools}:
                raise PortError(self.settings.error("model_profile_invalid"))
        return definitions

    def _payload(self, request: ModuleRequest[ReasoningInput]) -> dict:
        cfg = self.settings.model
        packet = {
            "model": cfg.name,
            "messages": self._messages(request),
            "tools": self._tools(request.input),
            "max_tokens": self.budget.plan(
                purpose=request.input.purpose
            ).output_reserved,
            "temperature": cfg.temperature,
            "thinking": {"type": "enabled" if cfg.thinking_enabled and request.input.purpose != "compaction" else "disabled"},
        }
        if self._explicit(request.input.purpose):
            packet["tool_choice"] = "auto"
        if cfg.thinking_enabled and request.input.purpose != "compaction":
            packet["reasoning_effort"] = cfg.thinking_mode
        return packet

    def _decision_from_message(self, message: dict, finish_reason: str | None, *, purpose="main"):
        if finish_reason == "length" and not message.get("content") and not message.get("tool_calls"):
            raise PortError(self.settings.error("model_output_incomplete"))
        if self._explicit(purpose):
            calls = message.get("tool_calls") or []
            terminal = [c for c in calls if c["function"]["name"] == self.settings.model.delivery_tool]
            if terminal:
                if len(calls) != 1:
                    raise ValueError("mixed_delivery_and_action")
                arguments = terminal[0]["function"]["arguments"]
                if finish_reason not in {"stop", "tool_calls"}:
                    text = DeliveryText().push(arguments)
                    if not text.strip():
                        raise PortError(self.settings.error("model_output_incomplete"))
                    return DecisionProposal(kind="answer", answer=text, incomplete=True,
                                            finish_reason=finish_reason or "missing_finish_reason")
                args = json.loads(arguments)
                if not isinstance(args, dict) or set(args) - {"text", "status", "complete", "remaining_work"} or not isinstance(args.get("text"), str) or not args["text"].strip():
                    raise ValueError("invalid_delivery_arguments")
                if not isinstance(args.get("complete", True), bool) or not isinstance(args.get("remaining_work", []), list) or not all(isinstance(v, str) and v.strip() for v in args.get("remaining_work", [])):
                    raise ValueError("invalid_delivery_arguments")
                status = args.get("status", "continuing" if args.get("complete") is False else "completed")
                if status not in {"completed", "continuing", "blocked"}:
                    raise ValueError("invalid_delivery_status")
                if status == "continuing" and not args.get("remaining_work"):
                    raise ValueError("stage_requires_remaining_work")
                return DecisionProposal(kind="answer", answer=args["text"], complete=status != "continuing", blocked=status == "blocked", remaining_work=args.get("remaining_work", []))
            if calls and finish_reason not in {"stop", "tool_calls"}:
                raise ValueError("missing_or_incomplete_control")
            if any(c["function"]["name"] == self.settings.model.clarification_tool for c in calls):
                if len(calls) != 1:
                    raise ValueError("mixed_wait_and_action")
                args = json.loads(calls[0]["function"]["arguments"])
                if not isinstance(args, dict):
                    raise ValueError("invalid_wait_arguments")
                questions = args.get("questions")
                if isinstance(questions, str):
                    questions = [questions]
                if not isinstance(questions, list) or not questions or not all(isinstance(q, str) and q.strip() for q in questions):
                    raise ValueError("invalid_wait_questions")
                return DecisionProposal(kind="needs_input", missing_inputs=questions)
        if finish_reason == "length":
            if not message.get("content"):
                raise PortError(self.settings.error("model_output_incomplete"))
            return DecisionProposal(
                kind="answer",
                answer=message["content"],
                incomplete=True,
                finish_reason=finish_reason,
            )
        if finish_reason not in ("stop", "tool_calls"):
            if message.get("content"):
                return DecisionProposal(
                    kind="answer",
                    answer=message["content"],
                    incomplete=True,
                    finish_reason=finish_reason or "missing_finish_reason",
                )
            raise PortError(self.settings.error("model_invalid_response"))
        calls = message.get("tool_calls") or []
        if calls:
            cfg = self.settings.model
            if any(
                call["function"]["name"] == cfg.clarification_tool for call in calls
            ):
                if len(calls) != 1:
                    raise PortError(
                        self.settings.error("invalid_decision", retryable=True)
                    )
                return DecisionProposal(
                    kind="needs_input",
                    missing_inputs=json.loads(calls[0]["function"]["arguments"])[
                        "questions"
                    ],
                )
            actions = [
                ActionRequest(
                    tool_name=call["function"]["name"],
                    arguments=json.loads(call["function"]["arguments"]),
                    action_id=call["id"],
                )
                for call in calls
            ]
            if len({action.action_id for action in actions}) != len(actions):
                raise PortError(self.settings.error("invalid_decision", retryable=True))
            return DecisionProposal(
                kind="action",
                action_requests=actions,
                commentary=message.get("content"),
            )
        return DecisionProposal(kind="answer", answer=message.get("content"))

    async def decide(self, request: ModuleRequest[ReasoningInput]) -> DecisionProposal:
        began = perf_counter()
        tokens = None
        status = "failed"
        try:
            response = await self.client.post(
                self.settings.model.endpoint,
                json=self._payload(request),
                headers={"x-zhouxu-purpose": request.input.purpose},
            )
            if response.status_code == 402:
                raise PortError(self.settings.error("model_quota_exhausted"))
            if response.status_code == 429 or response.status_code >= 500:
                raise PortError(
                    self.settings.error("model_unavailable", retryable=True)
                )
            if not response.is_success:
                raise PortError(self.settings.error("model_rejected"))
            data = response.json()
            usage = data.get("usage")
            if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
                tokens = usage["total_tokens"]
            choice = data["choices"][0]
            decision = self._decode_message(request, choice["message"], choice.get("finish_reason"))
            status = "ok"
            if decision.answer:
                decision.answer = PublicText(request.input.perceived.items).render(
                    decision.answer
                )
            return decision
        except httpx.RequestError:
            raise PortError(
                self.settings.error("model_unavailable", retryable=True)
            ) from None
        except (ValueError, KeyError, IndexError, TypeError, ValidationError):
            raise PortError(
                self.settings.error("model_invalid_response", retryable=True)
            ) from None
        finally:
            try:
                self.events.emit(
                    RunEvent(
                        run_id=request.run_id,
                        call_id=request.call_id,
                        module="model",
                        phase="model",
                        status=status,
                        elapsed_seconds=perf_counter() - began,
                        total_tokens=tokens,
                    )
                )
            except Exception:
                pass

    async def decide_stream(
        self,
        request: ModuleRequest[ReasoningInput],
        on_delta: Callable[[str], Awaitable[None]],
    ) -> DecisionProposal:
        """Read provider SSE and forward only visible answer text."""

        began = perf_counter()
        tokens = None
        status = "failed"
        calls: dict[int, dict] = {}
        readers: dict[int, DeliveryText] = {}
        explicit = self._explicit(request.input.purpose)
        content: list[str] = []
        reasoning: list[str] = []
        finish_reason: str | None = None
        plain_response: dict | None = None
        public = PublicText(request.input.perceived.items)

        def partial_answer():
            if explicit and readers:
                return "".join(reader.emitted for reader in readers.values())
            return "".join(content)
        try:
            payload = self._payload(request)
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
            async with self.client.stream(
                "POST",
                self.settings.model.endpoint,
                json=payload,
                headers={"x-zhouxu-purpose": request.input.purpose},
            ) as response:
                if response.status_code == 402:
                    raise PortError(self.settings.error("model_quota_exhausted"))
                if response.status_code == 429 or response.status_code >= 500:
                    raise PortError(
                        self.settings.error("model_unavailable", retryable=True)
                    )
                if not response.is_success:
                    raise PortError(self.settings.error("model_rejected"))
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    raw = line[5:].strip() if line.startswith("data:") else line.strip()
                    if not line.startswith("data:"):
                        if raw.startswith("{"):
                            try:
                                plain_response = json.loads(raw)
                            except json.JSONDecodeError:
                                pass
                        continue
                    if raw == "[DONE]":
                        break
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    usage = data.get("usage")
                    if isinstance(usage, dict) and isinstance(
                        usage.get("total_tokens"), int
                    ):
                        tokens = usage["total_tokens"]
                    choice = (data.get("choices") or [{}])[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}
                    if isinstance(delta.get("reasoning_content"), str):
                        reasoning.append(delta["reasoning_content"])
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        content.append(text)
                        visible = public.push(text)
                        if visible:
                            await on_delta(visible)
                    for part in delta.get("tool_calls") or []:
                        index = int(part.get("index", 0))
                        current = calls.setdefault(
                            index,
                            {
                                "id": part.get("id") or f"stream-call-{index}",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            },
                        )
                        if part.get("id"):
                            current["id"] = part["id"]
                        function = part.get("function") or {}
                        current["function"]["name"] += function.get("name") or ""
                        current["function"]["arguments"] += (
                            function.get("arguments") or ""
                        )
                        if explicit and current["function"]["name"] == self.settings.model.delivery_tool:
                            arguments = current["function"]["arguments"]
                            if arguments.strip():
                                visible = public.push(readers.setdefault(index, DeliveryText()).push(arguments))
                                if visible:
                                    await on_delta(visible)
            if plain_response is not None:
                data = plain_response
                usage = data.get("usage")
                if isinstance(usage, dict) and isinstance(
                    usage.get("total_tokens"), int
                ):
                    tokens = usage["total_tokens"]
                choice = (data.get("choices") or [{}])[0]
                decision = self._decode_message(request, choice["message"], choice.get("finish_reason"))
                if decision.answer:
                    decision.answer = public.render(decision.answer)
                status = "ok"
                return decision
            message = {"content": "".join(content) or None, "reasoning_content": "".join(reasoning)}
            if calls:
                message["tool_calls"] = [calls[index] for index in sorted(calls)]
            decision = self._decode_message(request, message, finish_reason)
            tail = public.push("", final=True)
            if tail:
                await on_delta(tail)
            if decision.answer:
                decision.answer = public.render(decision.answer)
            status = "ok"
            return decision
        except httpx.RequestError:
            if partial_answer():
                tail = public.push("", final=True)
                if tail:
                    await on_delta(tail)
                return DecisionProposal(
                    kind="answer",
                    answer=public.render(partial_answer()),
                    incomplete=True,
                    finish_reason="transport_error",
                )
            raise PortError(
                self.settings.error("model_unavailable", retryable=True)
            ) from None
        except (ValueError, KeyError, IndexError, TypeError, ValidationError):
            if partial_answer():
                tail = public.push("", final=True)
                if tail:
                    await on_delta(tail)
                return DecisionProposal(
                    kind="answer",
                    answer=public.render(partial_answer()),
                    incomplete=True,
                    finish_reason="invalid_response",
                )
            raise PortError(
                self.settings.error("model_invalid_response", retryable=True)
            ) from None
        finally:
            try:
                self.events.emit(
                    RunEvent(
                        run_id=request.run_id,
                        call_id=request.call_id,
                        module="model",
                        phase="model",
                        status=status,
                        elapsed_seconds=perf_counter() - began,
                        total_tokens=tokens,
                    )
                )
            except Exception:
                pass
