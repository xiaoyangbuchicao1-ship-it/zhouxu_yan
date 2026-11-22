"""Small, deterministic task-graph plans at the HTTP boundary.

Node 1 deliberately keeps planning narrow. A greeting is a real one-node
model run, while requests that may need tools stay on the existing Harness
loop until tool nodes and human gates have their full graph adapters. This
avoids showing a graph that cannot represent a tool result or a pending user
choice yet.
"""

from __future__ import annotations

import re

from backend.contracts.modules.loop import RunInput
from backend.contracts.runtime import NodeSpec, RunGraph

_GREETING = re.compile(
    r"^\s*(?:你好|您好|嗨|hi|hello|hey|在吗|早上好|下午好|晚上好|谢谢|谢啦)[！!。,.，、\s]*$",
    re.IGNORECASE,
)


def is_simple_chat(text: str) -> bool:
    """Return true only for a self-contained greeting/small courtesy turn."""

    return bool(_GREETING.fullmatch(text))


def _model_graph(*, instruction: str, graph_id: str | None = None) -> RunGraph:
    node = NodeSpec(
        id="model-answer",
        kind="model",
        label="回答",
        config={"instruction": instruction},
    )
    return RunGraph(**({"graph_id": graph_id} if graph_id else {}), nodes=[node])


def build_chat_graph(payload: RunInput) -> RunGraph | None:
    """Build the smallest safe graph for a chat turn, if one is sufficient.

    Tool-capable chat remains on the legacy Harness loop for this node. That
    loop already has the read-only tool allow-list and can ask for an explicit
    work-mode upgrade. Returning ``None`` is intentional and means “use that
    established executor”, not “silently skip the task”.
    """

    if payload.mode not in (None, "chat"):
        raise ValueError("chat_graph_requires_chat_mode")
    if not is_simple_chat(payload.text):
        return None
    return _model_graph(instruction="直接、简短地回应用户的问候。")


def build_work_graph(payload: RunInput) -> RunGraph:
    """Build a minimal work graph for callers that explicitly request one.

    Workflows involving Skill/tool/file nodes should submit their explicit
    graph in Node 1. The generic model node remains useful for a work-mode
    instruction that only needs a model response.
    """

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
    """Validate an explicit graph or return a safe automatic minimum graph."""

    if requested is not None:
        validate_graph_capabilities(requested, payload)
        return requested
    if payload.mode in (None, "chat"):
        return build_chat_graph(payload)
    if payload.mode == "work" and is_simple_chat(payload.text):
        # A work session can still contain a plain greeting. Keep the same
        # one-model presentation as chat; only requests that need tools stay
        # on the full Harness loop.
        return build_work_graph(payload)
    return None


__all__ = [
    "build_chat_graph",
    "build_work_graph",
    "is_simple_chat",
    "plan_graph",
    "validate_graph_capabilities",
]
