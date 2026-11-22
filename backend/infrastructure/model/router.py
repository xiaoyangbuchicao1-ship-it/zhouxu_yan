"""Per-run model routing over the existing Harness model ports."""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar

from backend.config.settings import Settings, load_key
from backend.contracts.modules.loop import DecisionProposal, ReasoningInput, ToolSpec
from backend.contracts.modules.perception import ContextItem
from backend.contracts.schemas.core import Error, ModuleRequest, PortError
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.model.directory import RuntimeModelRevision
from backend.infrastructure.model.protocols import adapter_for
from backend.infrastructure.model.registry import ModelRegistry
from backend.infrastructure.model.relay_config import load_relay_config


class RoutedModel:
    """One Harness-facing model that selects an adapter by immutable Run input."""

    def __init__(self, settings: Settings, events, *, registry=None):
        self.settings = settings
        self.events = events
        self.registry = registry or ModelRegistry(settings)
        self._adapters: dict[str, DeepSeekModel] = {}
        self._run_profiles: dict[str, str] = {}
        self._run_revisions: dict[str, str | None] = {}
        self._active_run: ContextVar[str | None] = ContextVar(
            f"model_run_{id(self)}", default=None
        )
        self._adapters["deepseek-flash"] = DeepSeekModel(
            settings, load_key(settings), events
        )

    def bind_run(
        self,
        run_id: str,
        profile_id: str | None,
        snapshot=None,
    ) -> None:
        if snapshot is not None and profile_id is not None and snapshot.profile_id != profile_id:
            raise PortError(Error(
                code="model_profile_changed",
                message="本次任务保存的模型与当前选择不一致，请重新开始。",
            ))
        selected = profile_id or (snapshot.profile_id if snapshot is not None else "deepseek-flash")
        requested_revision = getattr(snapshot, "profile_revision_id", None) if snapshot is not None else None
        if hasattr(self.registry, "resolve"):
            revision = self.registry.resolve(selected, profile_revision_id=requested_revision)
            profile = revision.profile
            revision_id = revision.profile_revision_id
        else:
            profile = self.registry.get(selected)
            revision = None
            revision_id = getattr(profile, "profile_revision_id", None)
        if not profile.configured and profile.provider != "deepseek":
            raise PortError(self.settings.error("model_profile_invalid"))
        # Historical snapshots remain routable after a profile is disabled;
        # only a new submission without a snapshot is blocked.
        if profile_id is not None and snapshot is None and not profile.available:
            raise PortError(self.settings.error("model_profile_invalid"))
        bound_profile = self._run_profiles.get(run_id)
        bound_revision = self._run_revisions.get(run_id)
        if bound_profile is not None and (bound_profile != selected or bound_revision != revision_id):
            raise PortError(self.settings.error("context_configuration"))
        if snapshot is not None:
            facts = {
                "provider": profile.provider,
                "model_id": profile.model_id,
                "protocol": profile.protocol,
            }
            for name, expected in facts.items():
                if getattr(snapshot, name, expected) != expected:
                    raise PortError(Error(
                        code="model_profile_changed",
                        message="本次任务保存的模型配置已变化，请重新开始。",
                    ))
            expected_profile_revision = getattr(snapshot, "profile_revision_id", None)
            if expected_profile_revision is not None and expected_profile_revision != revision_id:
                raise PortError(Error(
                    code="model_profile_changed",
                    message="本次任务保存的模型版本已不存在，无法继续。",
                ))
            expected_provider_revision = getattr(snapshot, "provider_revision_id", None)
            if expected_provider_revision is not None and revision is not None and expected_provider_revision != revision.provider_revision_id:
                raise PortError(Error(
                    code="model_profile_changed",
                    message="本次任务保存的连接版本已不存在，无法继续。",
                ))
            if getattr(snapshot, "thinking_mode", None) not in profile.thinking_modes:
                raise PortError(Error(
                    code="model_profile_changed",
                    message="本次任务保存的思考档位已不可用，请重新选择模型。",
                ))
            if snapshot.thinking_mode != self.settings.model.thinking_mode:
                raise PortError(Error(
                    code="model_profile_changed",
                    message="本次任务的思考档位与执行配置不一致，请重新开始。",
                ))
        self._run_profiles[run_id] = selected
        self._run_revisions[run_id] = revision_id
        self._active_run.set(run_id)

    def _adapter_for(self, run_id: str) -> DeepSeekModel:
        if run_id not in self._run_profiles:
            raise PortError(Error(
                code="model_run_unbound", message="任务尚未绑定模型，无法开始调用。"
            ))
        profile_id = self._run_profiles[run_id]
        revision_id = self._run_revisions.get(run_id)
        cache_key = profile_id if revision_id is None else (profile_id, revision_id)
        adapter = self._adapters.get(cache_key)
        if adapter is not None:
            return adapter
        selected = self.registry.runtime_settings(
            profile_id, profile_revision_id=revision_id
        ) if hasattr(self.registry, "runtime_settings") else self.settings
        revision = None
        if hasattr(self.registry, "resolve"):
            revision = self.registry.resolve(profile_id, profile_revision_id=revision_id)
        if revision is not None and hasattr(self.registry, "credential"):
            key = self.registry.credential(revision)
        else:
            key = (
                load_key(selected)
                if selected.model.provider == "deepseek"
                else load_relay_config(selected.model.credential_file).key
            )
        adapter = adapter_for(selected, key, self.events)
        self._adapters[cache_key] = adapter
        return adapter

    def _current(self) -> DeepSeekModel:
        return self._adapter_for(self._active_run.get() or "")

    def context_budget(self):
        return self._current().context_budget()

    def estimate_context(
        self, items: list[ContextItem], tools: list[ToolSpec], *, run_id: str
    ):
        return self._adapter_for(run_id).estimate_context(items, tools, run_id=run_id)

    def supports_images(self) -> bool:
        return self._current().supports_images()

    def allocate_tool_context(self, used_tokens: int, call_count: int, image_count: int = 0):
        return self._current().allocate_tool_context(used_tokens, call_count, image_count)

    async def decide(self, request: ModuleRequest[ReasoningInput]) -> DecisionProposal:
        return await self._adapter_for(request.run_id).decide(request)

    async def decide_stream(self, request, on_delta):
        return await self._adapter_for(request.run_id).decide_stream(request, on_delta)

    def export_run_state(self, run_id: str) -> dict:
        return self._adapter_for(run_id).export_run_state(run_id)

    def restore_run_state(self, run_id: str, value: dict) -> None:
        # The Run owns the model choice. A checkpoint may restore protocol
        # state, but must never silently replace that choice.
        self._adapter_for(run_id).restore_run_state(run_id, value)

    def close_run(self, run_id: str) -> None:
        profile_id = self._run_profiles.pop(run_id, None)
        revision_id = self._run_revisions.pop(run_id, None)
        cache_key = profile_id if revision_id is None else (profile_id, revision_id)
        adapter = self._adapters.get(cache_key)
        if adapter is not None:
            adapter.close_run(run_id)
        if self._active_run.get() == run_id:
            self._active_run.set(None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        for adapter in self._adapters.values():
            await adapter.__aexit__(None, None, None)
        self._adapters.clear()
        self._run_profiles.clear()
        self._run_revisions.clear()
        self._active_run.set(None)


@asynccontextmanager
async def open_routed_model(*, settings: Settings, events):
    model = RoutedModel(settings, events)
    async with model:
        yield model


__all__ = ["RoutedModel", "open_routed_model"]
