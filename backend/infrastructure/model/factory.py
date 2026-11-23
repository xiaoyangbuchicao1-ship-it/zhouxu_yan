"""Model adapter factory; selects wire adapter by protocol without vendor hardcodes."""

from contextlib import asynccontextmanager

from backend.config.settings import Settings, load_key
from backend.contracts.schemas.core import PortError
from backend.infrastructure.model.protocols import adapter_for
from backend.infrastructure.model.relay_config import load_relay_config


@asynccontextmanager
async def open_model(*, settings: Settings, events, role: str, collect_metrics=False):
    if role not in {"main", "review"}:
        raise ValueError("unknown_model_role")
    if settings.model.thinking_mode not in settings.model.modes:
        raise PortError(settings.error("model_profile_invalid"))
    selected = settings.model_copy(deep=True)
    if role == "review":
        selected.model.thinking_mode = "none"
        selected.model.decision_protocol = "text"
    try:
        key = load_key(selected)
    except PortError:
        if selected.model.provider == "relay":
            key = load_relay_config(selected.model.credential_file).key
        else:
            raise
    model = adapter_for(selected, key, events)
    async with model:
        yield model
