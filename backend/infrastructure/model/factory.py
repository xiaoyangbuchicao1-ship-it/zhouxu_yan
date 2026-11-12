"""One DeepSeek deployment; main effort and utility roles are independent."""

from contextlib import asynccontextmanager

from backend.config.settings import Settings, load_key
from backend.contracts.schemas.core import PortError
from backend.infrastructure.model.deepseek import DeepSeekModel


@asynccontextmanager
async def open_model(*, settings: Settings, events, role: str, collect_metrics=False):
    if role not in {"main", "review"}:
        raise ValueError("unknown_model_role")
    if settings.model_routing is not None or settings.model.thinking_mode not in settings.model.modes:
        raise PortError(settings.error("model_profile_invalid"))
    selected = settings.model_copy(deep=True)
    if role == "review":
        selected.model.thinking_mode = "none"
        selected.model.decision_protocol = "text"
    model = DeepSeekModel(selected, load_key(settings), events)
    async with model:
        yield model
