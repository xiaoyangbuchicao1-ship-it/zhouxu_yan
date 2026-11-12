"""The sole budget algorithm, shared by perception and the final wire guard."""

import json
import math

import httpx

from backend.contracts.model_context import RequestBudget, RequestEstimate
from backend.contracts.schemas.core import PortError


class ModelBudget:
    def __init__(self, settings, *, provider=None, role="main"):
        self.settings, self.policy, self.role = settings, settings.context_policy, role
        self.capability = settings.model_capabilities.get(settings.model.name)
        if self.capability is None or (
            provider and self.capability.provider != provider
        ):
            raise PortError(settings.error("model_profile_invalid"))
        self.deployment = settings.deployment_limits.get(settings.model.name)

    def plan(self, *, purpose="main", output_tokens=None) -> RequestBudget:
        cap, policy, deployment = self.capability, self.policy, self.deployment
        raw = cap.raw_context_tokens
        effective = raw or cap.conservative_context_tokens
        maximum = cap.max_generation_tokens or cap.conservative_generation_tokens
        if effective is None or maximum is None:
            raise PortError(self.settings.error("model_profile_invalid"))
        thinking = self.settings.model.thinking_enabled and purpose not in {"review", "compaction"}
        if thinking and not cap.generation_includes_thinking:
            raise PortError(self.settings.error("context_configuration"))
        if deployment and deployment.total_tokens:
            effective = min(effective, deployment.total_tokens)
        inputs = min(effective, cap.input_limit_tokens or effective)
        if deployment:
            inputs = min(inputs, deployment.input_tokens or inputs)
            maximum = min(maximum, deployment.output_tokens or maximum)
        requested = output_tokens or (
            policy.review_output_tokens
            if purpose == "review"
            else policy.summary_output_tokens
            if purpose == "compaction"
            else self.settings.model.generation_mode.output_tokens
        )
        reserved = min(requested, maximum)
        safety = math.ceil(effective * policy.safety_ratio)
        hard = min(inputs, effective - reserved - safety)
        if hard <= 0:
            raise PortError(self.settings.error("context_configuration"))
        target = min(math.floor(effective * policy.target_ratio), hard)
        return RequestBudget(
            model_id=cap.model_id,
            purpose=purpose,
            raw_tokens=raw,
            effective_tokens=effective,
            input_limit=inputs,
            output_reserved=reserved,
            safety_tokens=safety,
            hard=hard,
            target=target,
            warning=min(math.floor(effective * policy.warning_ratio), hard),
            low=min(math.floor(effective * policy.low_ratio), target),
            counting_method=cap.counting_method,
            assumed_capacity=raw is None,
        )

    def estimate(self, packet: dict) -> RequestEstimate:
        """UTF-8 bytes are a deliberately pessimistic text estimate, not base64 tokens.

        Includes serialized tools, envelopes, hidden continuation and protocol padding.
        Image capacity uses a separately verified per-image bound. Unknown media fail.
        """
        images = 0

        def project(value):
            nonlocal images
            if isinstance(value, dict):
                if value.get("type") in {"image_url", "input_image"}:
                    if (
                        self.capability.image_transport == "none"
                        or not self.capability.image_token_upper_bound
                    ):
                        raise PortError(self.settings.error("model_image_unsupported"))
                    images += 1
                    return {"type": "image", "capacity_accounted_separately": True}
                return {key: project(item) for key, item in value.items()}
            if isinstance(value, list):
                return [project(item) for item in value]
            if isinstance(value, str) and value.startswith("data:image/"):
                # Raw images must have been encoded as typed media before counting.
                raise PortError(self.settings.error("model_image_unsupported"))
            return value

        wire = project(packet)
        tokens = len(
            json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        tokens += self.policy.protocol_tokens
        tokens += images * (self.capability.image_token_upper_bound or 0)
        return RequestEstimate(
            tokens=tokens, method=self.capability.counting_method, image_count=images
        )

    def allocate_tool_context(self, used_tokens, call_count, image_count=0):
        budget = self.plan()
        available = max(
            0,
            budget.warning
            - used_tokens
            - self.policy.tool_envelope_tokens * max(1, call_count)
            - image_count * (self.capability.image_token_upper_bound or 0),
        )
        per_call = available // max(1, call_count)
        return {
            "tokens": per_call,
            "chars": min(
                self.policy.tool_resource_chars,
                per_call // self.policy.tool_char_token_bound,
            ),
        }

    def check(self, packet, *, purpose=None, enforce_warning=True):
        purpose = purpose or ("main" if packet.get("tools") else "review")
        output = packet.get("max_output_tokens", packet.get("max_tokens"))
        budget = self.plan(purpose=purpose, output_tokens=output)
        if output is None or output != budget.output_reserved:
            raise PortError(self.settings.error("context_configuration"))
        if packet.get("model") != self.capability.model_id:
            raise PortError(self.settings.error("model_profile_invalid"))
        estimate = self.estimate(packet)
        if (
            self.capability.max_images
            and estimate.image_count > self.capability.max_images
        ):
            raise PortError(self.settings.error("model_image_unsupported"))
        if (
            self.capability.max_request_bytes
            and len(json.dumps(packet, ensure_ascii=False).encode("utf-8"))
            > self.capability.max_request_bytes
        ):
            raise PortError(self.settings.error("context_budget_exceeded"))
        if estimate.tokens > budget.hard or (
            enforce_warning and estimate.tokens > budget.warning
        ):
            raise PortError(self.settings.error("context_budget_exceeded"))
        return budget, estimate


class BudgetTransport(httpx.AsyncBaseTransport):
    """Guard even direct client users, including the existing RAG reviewer."""

    def __init__(self, budget, inner=None):
        self.budget = budget
        self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request):
        if request.url != httpx.URL(self.budget.settings.model.endpoint):
            raise PortError(self.budget.settings.error("invalid_endpoint"))
        packet = json.loads(request.content)
        # Reviewer uses the authenticated client directly. Apply its own model/mode
        # and output policy here, before counting its complete encoded request.
        purpose = request.headers.get("x-zhouxu-purpose") or ("main" if packet.get("tools") else "review")
        plan = self.budget.plan(purpose=purpose)
        packet["max_tokens"] = plan.output_reserved
        packet["thinking"] = {
            "type": "enabled"
            if self.budget.settings.model.thinking_enabled and purpose not in {"review", "compaction"}
            else "disabled"
        }
        self.budget.check(packet, purpose=purpose)
        forwarded = httpx.Request(
            request.method,
            request.url,
            headers={
                k: v
                for k, v in request.headers.items()
                if k.lower() not in {"content-length", "x-zhouxu-purpose"}
            },
            json=packet,
            extensions=request.extensions,
        )
        return await self.inner.handle_async_request(forwarded)

    async def aclose(self):
        await self.inner.aclose()
