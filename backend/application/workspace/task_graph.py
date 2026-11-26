"""Explicit task-graph validation at the HTTP boundary.

Ordinary chat and work requests—including short greetings and courtesy turns—
stay on the unified Harness loop so they always share conversation history,
mode enforcement, and lazy execution-sandbox lifecycle. Only explicitly
supplied graphs enter GraphRunExecutor.
"""

from __future__ import annotations

from backend.contracts.modules.loop import RunInput
from backend.contracts.runtime import NodeSpec, RunGraph


def _model_graph(*, instruction: str, graph_id: str | None = None) -> RunGraph:
    node = NodeSpec(
        id="model-answer",
        kind="model",
        label="回答",
        config={"instruction": instruction},
    )
    return RunGraph(**({"graph_id": graph_id} if graph_id else {}), nodes=[node])


def build_work_graph(payload: RunInput) -> RunGraph:
    """Build a minimal work graph for callers that explicitly request one."""

    if payload.mode != "work":
        raise ValueError("work_graph_requires_work_mode")
    return _model_graph(instruction="完成用户目标，并直接给出可交付的结果。")


_CHAT_FORBIDDEN_KINDS = frozenset(
    {"skill", "file", "human_gate", "compile", "subgraph", "transform"}
)


def validate_graph_capabilities(graph: RunGraph, payload: RunInput) -> None:
    """Reject graph capabilities that the Run mode cannot legally use."""

    if payload.mode == "work":
        return
    if payload.mode not in (None, "chat"):
        raise ValueError("unsupported_run_mode")
    for node in graph.nodes:
        if node.kind in _CHAT_FORBIDDEN_KINDS:
            raise ValueError("work_mode_required")
        if node.kind == "tool":
            tool_name = node.config.get("tool_name")
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ValueError("invalid_graph_tool")
            # The concrete ToolSpec is checked by the registered graph
            # adapter. Node 1 only blocks capability classes that are always
            # privileged; read-only tool resolution stays at that boundary.


def plan_graph(payload: RunInput, *, requested: RunGraph | None = None) -> RunGraph | None:
    """Validate an explicit graph; otherwise stay on the unified Harness loop."""

    if requested is not None:
        validate_graph_capabilities(requested, payload)
        return requested
    return None


__all__ = [
    "build_work_graph",
    "plan_graph",
    "validate_graph_capabilities",
]
