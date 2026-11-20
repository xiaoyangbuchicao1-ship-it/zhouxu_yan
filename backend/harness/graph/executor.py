"""Minimal sequential graph adapter owned by the existing Run scheduler.

Parallelism, human gates and provider routing are later milestones. This slice
executes registered node adapters in dependency order and persists every safe
node boundary through the scheduler's existing checkpoint callback.
"""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from time import monotonic

from backend.contracts.events.runtime import RunEvent
from backend.contracts.harness.lifecycle import RunEnvelope
from backend.contracts.modules.loop import RunResult
from backend.contracts.runtime import (
    Attempt,
    Checkpoint,
    GraphNodeResult,
    GraphProgress,
    NodeSpec,
    NodeState,
    Ref,
    RunGraph,
)
from backend.contracts.schemas.core import Error, PortError
from backend.harness.lifecycle.control import RunControl, StopRequested

NodeHandler = Callable[[NodeSpec, RunEnvelope, dict, RunControl], Awaitable[GraphNodeResult]]


def validate_graph(graph: RunGraph) -> list[NodeSpec]:
    """Validate an external graph and return a stable topological order."""

    nodes = {node.id: node for node in graph.nodes}
    if len(nodes) != len(graph.nodes):
        raise ValueError("duplicate_graph_node")
    dependencies = {node.id: set(node.depends_on) for node in graph.nodes}
    for edge in graph.edges:
        if edge.from_node not in nodes or edge.to_node not in nodes:
            raise ValueError("graph_edge_node_not_found")
        if edge.condition is not None:
            raise ValueError("conditional_edges_not_supported_yet")
        dependencies[edge.to_node].add(edge.from_node)
    for node_id, required in dependencies.items():
        if node_id in required or not required.issubset(nodes):
            raise ValueError("invalid_graph_dependency")
    ordered = []
    remaining = set(nodes)
    while remaining:
        ready = [node for node in graph.nodes if node.id in remaining and not dependencies[node.id] & remaining]
        if not ready:
            raise ValueError("cyclic_graph")
        ordered.extend(ready)
        remaining.difference_update(node.id for node in ready)
    return ordered


class GraphRunExecutor:
    """Execute typed nodes without adding another queue or Run store."""

    def __init__(self, handlers: dict[str, NodeHandler], *, events=None):
        self.handlers = dict(handlers)
        self.events = events

    async def execute(self, run: RunEnvelope, control: RunControl) -> RunResult:
        started = monotonic()
        graph = run.graph
        ordered = validate_graph(graph)
        progress = GraphProgress(
            node_states={node.id: run.node_states.get(node.id, NodeState(node_id=node.id, kind=node.kind, label=node.label, input_refs=node.input_refs)) for node in ordered},
            attempts=list(run.attempts),
            artifacts=list(run.graph_artifacts),
            state=dict(run.execution_state),
        )
        summaries = list(progress.state.get("graph_summaries", []))

        async def publish():
            progress.elapsed_ms = int((monotonic() - started) * 1000)
            progress.checkpoint = Checkpoint(
                run_id=run.run_id,
                graph_version=graph.version,
                completed_node_ids=[key for key, state in progress.node_states.items() if state.status == "succeeded"],
                active_node_id=progress.current_node_id,
                pending_node_ids=[key for key, state in progress.node_states.items() if state.status == "queued"],
                event_sequence=run.last_sequence,
            )
            progress.state["graph_summaries"] = summaries
            if control.graph_progress:
                await control.graph_progress(progress)

        def emit(node, attempt, phase, status, error=None):
            if self.events:
                self.events.emit(RunEvent(run_id=run.run_id, module="graph", phase=phase, event_type="node", node_id=node.id, attempt_id=attempt.attempt_id, status=status, error_code=error.code if error else None))

        try:
            for node in ordered:
                state = progress.node_states[node.id]
                if state.status == "succeeded":
                    continue
                if control.cancelled:
                    raise StopRequested
                if node.kind not in self.handlers:
                    raise ValueError(f"node_executor_unavailable:{node.kind}")
                if node.side_effect == "external" and not run.runtime_policy.allow_external_side_effects:
                    raise ValueError("external_side_effect_not_allowed")
                state.status = "running"
                state.attempt += 1
                state.started_at = datetime.now(timezone.utc)
                state.finished_at = None
                attempt = Attempt(run_id=run.run_id, node_id=node.id, number=state.attempt, idempotency_key=hashlib.sha256(f"{run.run_id}:{node.id}".encode()).hexdigest())
                progress.attempts.append(attempt)
                progress.current_node_id, progress.current_attempt_id = node.id, attempt.attempt_id
                emit(node, attempt, "started", "running")
                await publish()
                node_started = monotonic()
                operation = self.handlers[node.kind](node, run, progress.state, control)
                timeout = node.timeout_ms or run.runtime_policy.timeout_ms
                try:
                    result = await asyncio.wait_for(operation, timeout / 1000) if timeout else await operation
                except (PortError, TimeoutError) as exc:
                    error = exc.error if isinstance(exc, PortError) else Error(code="node_timeout", message="节点执行超时", retryable=True)
                    state.status, state.error = "failed", error
                    attempt.status, attempt.error = "failed", error
                    raise PortError(error) from exc
                finally:
                    state.finished_at = datetime.now(timezone.utc)
                    state.duration_ms = int((monotonic() - node_started) * 1000)
                    attempt.finished_at = state.finished_at
                state.status, attempt.status = "succeeded", "succeeded"
                state.output_refs = result.output_refs
                attempt.provider_request_id = result.provider_request_id
                progress.artifacts.extend(result.artifacts)
                if result.summary:
                    summaries.append(result.summary)
                emit(node, attempt, "finished", "succeeded")
                progress.current_node_id = progress.current_attempt_id = None
                await publish()
            return RunResult(run_id=run.run_id, status="succeeded", answer="\n\n".join(summaries), elapsed_seconds=monotonic() - started, completed_units=len(ordered), total_units=len(ordered), execution_state=progress.state)
        except StopRequested:
            await publish()
            return RunResult(run_id=run.run_id, status="cancelled", answer="\n\n".join(summaries), elapsed_seconds=monotonic() - started, execution_state=progress.state)
        except PortError as exc:
            if progress.current_node_id:
                emit(node, attempt, "finished", "failed", exc.error)
            await publish()
            return RunResult(run_id=run.run_id, status="failed", error=exc.error, elapsed_seconds=monotonic() - started, execution_state=progress.state)
        except Exception as exc:
            error = Error(code="node_execution_failed", message=f"节点执行失败：{type(exc).__name__}")
            state = progress.node_states.get(progress.current_node_id or "")
            if state is not None:
                state.status = "failed"
                state.error = error
            if progress.current_node_id:
                emit(node, attempt, "finished", "failed", error)
            await publish()
            return RunResult(run_id=run.run_id, status="failed", error=error, elapsed_seconds=monotonic() - started, execution_state=progress.state)


async def descriptor_node(node: NodeSpec, run: RunEnvelope, state: dict, control: RunControl) -> GraphNodeResult:
    """Small first-slice adapter for declarative Skill/tool/model/file nodes.

    It records a stable output reference and a user-visible summary. Real
    provider/tool routing is deliberately added in the model/tool milestone;
    the runtime contract is already the same for both paths.
    """

    if control.cancelled:
        raise StopRequested
    summary = node.config.get("summary") or f"{node.label} 已完成"
    ref = Ref(name=node.id, kind="output", media_type="text/plain")
    state.setdefault("node_outputs", {})[node.id] = {"summary": summary, "ref_id": ref.ref_id}
    await asyncio.sleep(0)
    return GraphNodeResult(output_refs=[ref], summary=str(summary))


def default_graph_handlers() -> dict[str, NodeHandler]:
    return {kind: descriptor_node for kind in ("skill", "tool", "model", "file")}


class RoutedRunExecutor:
    """Route graph Runs and legacy chat through one scheduler executor port."""

    def __init__(self, legacy, graph: GraphRunExecutor):
        self.legacy, self.graph = legacy, graph

    async def execute(self, run, control):
        return await (self.graph if run.graph else self.legacy).execute(run, control)
