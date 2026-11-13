"""Composition root: the only place that connects concrete modules to the runtime."""

from contextlib import AsyncExitStack, asynccontextmanager

from backend.config.settings import Settings
from backend.contracts.modules.loop import ToolSpec
from backend.contracts.ports import (
    ContextualToolExecutor,
    EventSink,
    ExecutionProvider,
    ModelContextPort,
    ModelPort,
    Module,
    StreamSink,
    ToolExecutor,
)
from backend.contracts.schemas.core import PortError
from backend.harness.runtime.context_recovery import ContextRecovery
from backend.harness.runtime.loop import Harness
from backend.infrastructure.parsing.catalog import MaterialCatalog
from backend.modules.action.service import RegisteredAction
from backend.modules.memory.service import InMemoryMemory
from backend.modules.perception.service import TextPerception
from backend.modules.reasoning.service import ModelReasoning
from backend.modules.reflection.service import RuleReflection


def build_harness(
    *,
    model: ModelPort,
    settings: Settings,
    events: EventSink,
    streams: StreamSink | None = None,
    tools: dict[str, tuple[ToolSpec, ToolExecutor | ContextualToolExecutor]]
    | None = None,
    rag: Module | None = None,
    model_context: ModelContextPort | None = None,
    execution_tools: ExecutionProvider | None = None,
) -> Harness:
    context_model = model_context or model
    if not isinstance(context_model, ModelContextPort):
        raise PortError(settings.error("context_configuration"))
    registered = dict(tools or {})
    if execution_tools is not None:
        for name, pair in execution_tools.registrations().items():
            if name in registered:
                raise ValueError("duplicate_tool_registration")
            registered[name] = pair
    if rag is not None:
        from backend.infrastructure.tools.rag import RagSearchTool

        bridge = RagSearchTool(rag, settings)
        if bridge.spec.name in registered:
            raise ValueError("duplicate_tool_registration")
        registered[bridge.spec.name] = (bridge.spec, bridge)
    catalog = MaterialCatalog(settings)
    from backend.infrastructure.tools.material_read import MaterialReadTool
    from backend.infrastructure.tools.material_search import MaterialSearchTool

    material_tool = MaterialReadTool(catalog, settings)
    material_search_tool = MaterialSearchTool(catalog, settings)
    if material_tool.spec.name in registered:
        raise ValueError("duplicate_tool_registration")
    registered[material_tool.spec.name] = (material_tool.spec, material_tool)
    if material_search_tool.spec.name in registered:
        raise ValueError("duplicate_tool_registration")
    registered[material_search_tool.spec.name] = (
        material_search_tool.spec,
        material_search_tool,
    )
    # Maritime research is an additive set of read-only business tools.  The
    # corpus adapter is shared by search and later citation projection, while
    # the existing Harness remains the sole dispatcher.
    from backend.application.maritime.work_packages import (
        WorkPackageTool,
        configured_store,
    )
    from backend.infrastructure.search.maritime_corpus import MaritimeCorpus
    from backend.infrastructure.tools.maritime import ClaimTool, CorpusTool

    maritime_corpus = MaritimeCorpus()
    for maritime_tool in (
        CorpusTool(maritime_corpus),
        ClaimTool(),
        WorkPackageTool(configured_store(settings), maritime_corpus),
    ):
        if maritime_tool.spec.name in registered:
            raise ValueError("duplicate_tool_registration")
        registered[maritime_tool.spec.name] = (
            maritime_tool.spec,
            maritime_tool,
        )
    # Skills are read-only method packages.  The model-facing read_skill tool
    # is registered independently of RAG, case retrieval, or execution so the
    # Skill catalog remains usable in ordinary chat and work conversations.
    from backend.infrastructure.tools.skills import SkillReadTool
    from backend.skills.registry import default_skill_registry

    skill_tool = SkillReadTool(default_skill_registry(), settings)
    if skill_tool.spec.name in registered:
        raise ValueError("duplicate_tool_registration")
    registered[skill_tool.spec.name] = (skill_tool.spec, skill_tool)
    for name, (spec, _) in registered.items():
        if name != spec.name or name == settings.model.clarification_tool:
            raise ValueError("invalid_tool_registration")
    return Harness(
        perception=TextPerception(
            settings, model_context=context_model, streams=streams
        ),
        memory=InMemoryMemory(
            max_items=settings.memory.max_recall_items,
            unified_budget=True,
        ),
        reasoning=ModelReasoning(model, settings, streams=streams),
        action=RegisteredAction(
            {name: pair[1] for name, pair in registered.items()}, settings
        ),
        reflection=RuleReflection(settings),
        settings=settings,
        tools=[
            pair[0]
            for name, pair in registered.items()
            if name not in {material_tool.spec.name, material_search_tool.spec.name}
        ],
        events=events,
        streams=streams,
        material_catalog=catalog,
        material_tool=material_tool,
        material_search_tool=material_search_tool,
        model_context=context_model,
        context_recovery=ContextRecovery(model, context_model, settings, catalog),
        execution_tools=execution_tools,
    )


@asynccontextmanager
async def open_rag(
    *, settings: Settings, events: EventSink, dynamic: bool | None = None
):
    """Independent retrieval entry; warm the resident encoder before accepting work.

    Optional imports keep ordinary Harness startup independent of the rag extra.
    This does not register an action tool or alter the existing main loop.
    ``dynamic`` is the legacy diagnostic override for inner model review;
    normal callers follow rag.review_enabled. The outer Agent can still requery.
    """
    from backend.infrastructure.model.factory import open_model
    from backend.infrastructure.search.bge_m3 import BgeM3Encoder
    from backend.infrastructure.search.deepseek_review import DeepSeekEvidenceReviewer
    from backend.infrastructure.search.opensearch import OpenSearchRetriever
    from backend.modules.dynamic_rag.service import DynamicRag

    encoder = BgeM3Encoder(settings)
    try:
        active_encoder = encoder
        try:
            await encoder.encode(settings.rag.warmup_text)
        except PortError:
            if not settings.rag.allow_bm25_fallback:
                raise
            active_encoder = None
        async with AsyncExitStack() as stack:
            search = await stack.enter_async_context(
                OpenSearchRetriever(settings, active_encoder)
            )
            reviewer = None
            review_enabled = settings.rag.review_enabled if dynamic is None else dynamic
            if review_enabled:
                model = await stack.enter_async_context(
                    open_model(settings=settings, events=events, role="review")
                )
                reviewer = DeepSeekEvidenceReviewer(model)
            yield DynamicRag(search, settings, reviewer)
    finally:
        encoder.close()


@asynccontextmanager
async def open_harnesses(
    *, settings: Settings, events: EventSink, modes: tuple[str, ...]
):
    """Application lifetime: warm the encoder once, then serve multiple runs.

    Startup performs no retrieval. Only an action requested by the model queries
    the corpus. Tests may still inject a Module via build_harness(rag=...).
    """
    from backend.infrastructure.model.factory import open_model

    async with AsyncExitStack() as stack:
        execution_tools = None
        if settings.execution.enabled:
            if settings.execution.provider == "native":
                from backend.infrastructure.execution.native import NativeProvider

                execution_tools = NativeProvider(settings)
            else:
                from backend.infrastructure.execution.docker import DockerProvider

                execution_tools = DockerProvider(settings)
            stack.push_async_callback(execution_tools.close_all)
        rag = None
        retrieval = None
        case_service = None
        if settings.rag_tool.enabled or settings.cases.enabled:
            try:
                retrieval = await stack.enter_async_context(
                    open_rag(settings=settings, events=events)
                )
            except PortError as exc:
                # Keep basic Q&A available. Retrieval reports the actual setup
                # failure when requested; never pretend a missing service is empty.
                from backend.contracts.schemas.core import ModuleResponse

                class UnavailableRetrieval:
                    def __init__(self, error):
                        self.error = error

                    async def handle(self, request):
                        return ModuleResponse(status="failed", error=self.error)

                retrieval = UnavailableRetrieval(exc.error)
        if settings.rag_tool.enabled:
            rag = retrieval
        if settings.cases.enabled:
            import httpx

            from backend.infrastructure.search.cases import CaseRetriever

            try:
                if retrieval is not None and hasattr(
                    getattr(retrieval, "search", None), "client"
                ):
                    case_service = CaseRetriever(
                        settings, retrieval.search.encoder, retrieval.search.client
                    )
                    await case_service.validate_index()
            except (OSError, ValueError, KeyError, AttributeError, httpx.HTTPError):
                # Register truthful unavailable tools. Law retrieval and unrelated
                # chat remain usable; never report an unavailable corpus as empty.
                case_service = None
            if case_service is None:
                from backend.contracts.events.runtime import RunEvent

                events.emit(
                    RunEvent(
                        run_id="startup",
                        module="cases",
                        phase="finished",
                        status="failed",
                        error_code="case_service_unavailable",
                    )
                )
        harnesses = {}
        for mode in dict.fromkeys(modes):
            if mode not in settings.model.modes:
                raise ValueError("unsupported_thinking_mode")
            selected = settings.model_copy(deep=True)
            selected.model.thinking_mode = mode
            model = await stack.enter_async_context(
                open_model(settings=selected, events=events, role="main")
            )
            harnesses[mode] = build_harness(
                model=model,
                settings=selected,
                events=events,
                streams=events if hasattr(events, "emit_stream") else None,
                rag=rag,
                tools=_case_tools(settings, case_service),
                execution_tools=execution_tools,
            )
        yield harnesses


def _case_tools(settings, service):
    if not settings.cases.enabled:
        return {}
    from backend.infrastructure.tools.cases import CaseTool

    tools = [CaseTool(service, settings), CaseTool(service, settings, read=True)]
    return {tool.spec.name: (tool.spec, tool) for tool in tools}


@asynccontextmanager
async def open_harness(*, settings: Settings, events: EventSink):
    """CLI uses the configured mode; the workbench opens all modes.

    Each mode has its own model client, budget and protocol continuation cache.
    All workbench modes share one resident retrieval service/encoder.
    """
    async with open_harnesses(
        settings=settings, events=events, modes=(settings.model.thinking_mode,)
    ) as harnesses:
        yield harnesses[settings.model.thinking_mode]
