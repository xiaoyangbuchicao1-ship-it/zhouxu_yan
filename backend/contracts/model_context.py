"""Provider facts, deployment limits and application policy are separate records."""

from typing import Literal

from pydantic import Field, model_validator

from backend.contracts.schemas.core import Contract


class ModelCapabilities(Contract):
    provider: str
    model_id: str
    version_or_alias: str
    raw_context_tokens: int | None = Field(default=None, gt=0)
    input_limit_tokens: int | None = Field(default=None, gt=0)
    max_generation_tokens: int | None = Field(default=None, gt=0)
    generation_includes_thinking: bool
    image_transport: Literal["none", "chat_user_data_url", "responses_input_image"]
    image_token_upper_bound: int | None = Field(default=None, gt=0)
    max_request_bytes: int | None = Field(default=None, gt=0)
    max_images: int | None = Field(default=None, gt=0)
    counting_method: Literal["utf8_upper_bound_estimate"] = "utf8_upper_bound_estimate"
    source: list[str]
    checked_on: str
    notes: str
    # These are explicitly unverified operating assumptions, NEVER raw capacity.
    conservative_context_tokens: int | None = Field(default=None, gt=0)
    conservative_generation_tokens: int | None = Field(default=None, gt=0)


class DeploymentLimits(Contract):
    total_tokens: int | None = Field(default=None, gt=0)
    input_tokens: int | None = Field(default=None, gt=0)
    output_tokens: int | None = Field(default=None, gt=0)
    source: str = "No additional confirmed deployment limits"


class ContextPolicy(Contract):
    low_ratio: float = Field(default=0.60, gt=0, lt=1)
    target_ratio: float = Field(default=0.65, gt=0, lt=1)
    warning_ratio: float = Field(default=0.70, gt=0, lt=1)
    safety_ratio: float = Field(default=0.05, ge=0, lt=1)
    protocol_tokens: int = Field(default=256, ge=0)
    review_output_tokens: int = Field(default=1400, gt=0)
    summary_output_tokens: int = Field(default=1600, gt=0)
    max_compactions: int = Field(default=3, ge=1, le=8)
    compaction_seconds: float = Field(default=20.0, gt=0)
    tool_resource_chars: int = Field(default=24000, gt=0)
    tool_envelope_tokens: int = Field(default=2048, ge=0)
    tool_char_token_bound: int = Field(default=4, ge=1)
    material_excerpt_chars: int = Field(default=4000, gt=0)
    repeated_tool_limit: int = Field(default=2, ge=1)
    inventory_enabled: bool = True
    inventory_goal_chars: int = Field(default=1000, gt=0)
    inventory_max_sources: int = Field(default=8, ge=1)
    inventory_max_operations: int = Field(default=6, ge=1)

    @model_validator(mode="after")
    def ordered(self):
        if not self.low_ratio <= self.target_ratio <= self.warning_ratio:
            raise ValueError("context_ratios_out_of_order")
        return self


class RequestBudget(Contract):
    model_id: str
    purpose: str
    raw_tokens: int | None
    effective_tokens: int
    input_limit: int
    output_reserved: int
    safety_tokens: int
    hard: int
    target: int
    warning: int
    low: int
    counting_method: str
    assumed_capacity: bool = False


class RequestEstimate(Contract):
    tokens: int = Field(ge=0)
    method: str
    image_count: int = 0
    estimated: bool = True
