"""Public model profile contracts.

Provider facts and application choices are deliberately kept separate.  A
profile may be shown to a user without ever exposing the credential used to
call it.
"""

from typing import Literal

from pydantic import Field

from backend.contracts.schemas.core import Contract

ModelProtocol = Literal[
    "deepseek_chat_completions",
    "openai_chat_completions",
    "openai_responses",
    "anthropic_messages",
    "google_generative_language",
]
CapabilityState = Literal["confirmed", "unsupported", "unconfirmed"]
ModelFamily = Literal["deepseek", "gpt", "claude", "gemini", "glm", "other"]


class ModelCapabilityFlags(Contract):
    """What the adapter has proved, rather than what a model name suggests."""

    tools: CapabilityState = "unconfirmed"
    thinking: CapabilityState = "unconfirmed"
    streaming: CapabilityState = "unconfirmed"
    structured_output: CapabilityState = "unconfirmed"
    vision: CapabilityState = "unsupported"


class ModelProfile(Contract):
    """A secret-free, selectable model profile."""

    profile_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    # Immutable revision identities let a new Run follow the current model
    # configuration while an existing Run keeps the exact connection/profile
    # facts it accepted.  They are optional only for pre-migration history.
    provider_revision_id: str | None = None
    profile_revision_id: str | None = None
    revision: int = Field(default=1, ge=1)
    family: ModelFamily
    label: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    protocol: ModelProtocol
    endpoint: str | None = None
    context_tokens: int | None = Field(default=None, gt=0)
    max_generation_tokens: int | None = Field(default=None, gt=0)
    context_confirmed: bool = False
    thinking_modes: list[Literal["none", "low", "medium", "high", "xhigh", "max"]] = Field(
        default_factory=lambda: ["none"]
    )
    default_thinking_mode: Literal["none", "low", "medium", "high", "xhigh", "max"] = "none"
    capabilities: ModelCapabilityFlags = Field(default_factory=ModelCapabilityFlags)
    configured: bool = False
    available: bool = False
    availability_note: str = ""
    source: str = Field(min_length=1)
    checked_on: str = Field(min_length=10)


class ModelFamilyGroup(Contract):
    family: ModelFamily
    label: str
    models: list[ModelProfile]


class ModelCatalogView(Contract):
    """The only model data that may cross the authenticated API boundary."""

    groups: list[ModelFamilyGroup]
    default_profile_id: str


class ModelRunSnapshot(Contract):
    """Immutable public facts copied into a Run at submission time."""

    profile_id: str
    provider: str
    provider_revision_id: str | None = None
    profile_revision_id: str | None = None
    profile_revision: int | None = Field(default=None, ge=1)
    model_id: str
    protocol: ModelProtocol
    thinking_mode: Literal["none", "low", "medium", "high", "xhigh", "max"]
    context_tokens: int | None = None
    max_generation_tokens: int | None = None
    capabilities: ModelCapabilityFlags
    checked_on: str
