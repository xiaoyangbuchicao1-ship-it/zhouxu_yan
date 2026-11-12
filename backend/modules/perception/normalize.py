from __future__ import annotations

from typing import Iterable

from pydantic import ValidationError

from backend.contracts.modules.loop import (
    ActionResult,
    DecisionProposal,
    FeedbackRecord,
    RunInput,
)
from backend.contracts.modules.memory import MemoryRecord, TaskState
from backend.contracts.modules.perception import ContextItem, ContextLink


def normalize_context(
    current: RunInput,
    records: Iterable[FeedbackRecord | MemoryRecord],
    candidates: Iterable[ContextItem],
    *,
    run_id: str | None = None,
    task_state: TaskState | None = None,
) -> list[ContextItem]:
    """Convert heterogeneous inputs into attributed, linked context items.

    This function only performs deterministic protocol work. It does not infer
    maritime facts, resolve semantic contradictions, or call a model.
    """

    items = [
        ContextItem(
            id="current-user",
            type="message",
            source="user",
            kind="user.request",
            role="user",
            body={
                "goal": current.text.strip(),
                "facts": current.facts,
                "constraints": current.constraints,
            },
        )
    ]
    seen = {}
    pending: dict[str, tuple[str, str]] = {}

    for record in records:
        previous = seen.get(record.record_id)
        if previous is not None:
            if previous != record:
                raise ValueError("conflicting_record")
            continue
        seen[record.record_id] = record

        # The current request already has a stable, dedicated position.
        payload = record.payload if isinstance(record, MemoryRecord) else record.updates
        if record.source == "tool_reference":
            payload = _source_reference(payload)
        canonical = isinstance(record, MemoryRecord) and record.run_id is not None
        if (
            record.source == "user"
            and payload == current.model_dump()
            and (not canonical or record.run_id == run_id)
        ):
            continue

        if isinstance(record, MemoryRecord) and record.status != "available":
            items.append(
                ContextItem(
                    id=record.record_id,
                    type="message",
                    source=record.source,
                    kind=record.kind,
                    role="user",
                    status=record.status,
                    body={},
                    links=[ContextLink(**link.model_dump()) for link in record.links],
                )
            )
            continue

        if record.source == "reasoning":
            proposal = DecisionProposal.model_validate(payload["proposal"])
            if proposal.kind == "action":
                if not proposal.action_requests:
                    raise ValueError("invalid_tool_requests")
                action_ids = [action.action_id for action in proposal.action_requests]
                if len(set(action_ids)) != len(action_ids):
                    raise ValueError("invalid_tool_requests")
                pending.update(
                    {
                        action.action_id: (record.record_id, action.tool_name)
                        for action in proposal.action_requests
                    }
                )
                items.append(
                    ContextItem(
                        id=record.record_id,
                        type="tool_call",
                        source="reasoning",
                        kind="reasoning.proposal",
                        role="assistant",
                        body={
                            "text": proposal.commentary,
                            "calls": [
                                action.model_dump()
                                for action in proposal.action_requests
                            ]
                        },
                    )
                )
            else:
                body = {
                    "kind": proposal.kind,
                    "text": proposal.answer or "",
                    "missing_inputs": proposal.missing_inputs,
                    "assumptions": proposal.assumptions,
                }
                items.append(
                    ContextItem(
                        id=record.record_id,
                        type="message",
                        source="reasoning",
                        kind="reasoning.proposal",
                        role="assistant",
                        body=body,
                    )
                )
            continue

        if record.source == "action":
            action = ActionResult.model_validate(payload["action"])
            expected = pending.get(action.action_id)
            if expected is None or expected[1] != action.tool_name:
                raise ValueError("unmatched_tool_result")
            proposal_id, _ = pending.pop(action.action_id)
            items.append(
                ContextItem(
                    id=record.record_id,
                    type="tool_result",
                    source="action",
                    kind="action.result",
                    role="tool",
                    body=action.model_dump(),
                    links=[ContextLink(relation="responds_to", target_id=proposal_id)],
                )
            )
            continue

        # A non-action record cannot interrupt an unfinished tool exchange.
        if pending:
            raise ValueError("incomplete_tool_exchange")

        if record.summary or payload or canonical:
            items.append(
                ContextItem(
                    id=record.record_id,
                    type="message",
                    source=record.source,
                    kind=f"{record.source}.feedback",
                    role="user",
                    body={"summary": record.summary, "updates": payload},
                )
            )

    if pending:
        raise ValueError("incomplete_tool_exchange")

    # Enrich the canonical envelope after protocol conversion, without mixing
    # body-supplied metadata into source/status/links.
    historical = set()
    for item in items:
        record = seen.get(item.id)
        if not isinstance(record, MemoryRecord) or record.run_id is None:
            continue
        item.kind = record.kind
        item.status = record.status
        item.origin_run_id = record.run_id
        if record.source == "user" and record.run_id == run_id:
            item.body["relation"] = "earlier_input_in_this_run"
            item.body["current_request_id"] = "current-user"
        item.identity_labels = _identity_labels(record.payload)
        links = {(link.relation, link.target_id) for link in item.links}
        item.links.extend(
            ContextLink(**link.model_dump())
            for link in record.links
            if (link.relation, link.target_id) not in links
        )
        if record.run_id != run_id:
            historical.add(item.id)
    items = [i for i in items if i.id in historical] + [
        i for i in items if i.id not in historical
    ]
    if task_state is not None:
        items.insert(
            0,
            ContextItem(
                id=f"task-state:{task_state.task_id}",
                type="message",
                source="memory",
                kind="task.state",
                role="user",
                body=task_state.model_dump(mode="json"),
            ),
        )

    _append_candidates(items, candidates)
    return items


def _append_candidates(
    items: list[ContextItem], candidates: Iterable[ContextItem]
) -> None:
    """Place current resources beside the request, before its execution history."""

    existing = {item.id: item for item in items}
    insertion = next(
        (index + 1 for index, item in enumerate(items) if item.id == "current-user"),
        len(items),
    )
    for candidate in candidates:
        candidate = candidate.model_copy(deep=True)
        # Candidate payloads are data, never runtime tool protocol or instruction roles.
        candidate.role = "user"
        if candidate.type in {"tool_call", "tool_result"}:
            candidate.type = "resource"
        if candidate.status not in {"available", "partial", "failed", "unavailable"}:
            candidate.body = {}
        previous = existing.get(candidate.id)
        if previous is not None:
            if previous != candidate:
                raise ValueError("conflicting_context_item")
            continue
        # Keep the source opening before later reads without moving or splitting
        # any native assistant/tool exchange in the execution records.
        items.insert(insertion, candidate)
        insertion += 1
        existing[candidate.id] = candidate


def validate_normalized_items(items: Iterable[ContextItem]) -> None:
    """Force validation at the boundary for callers constructing raw models."""

    try:
        for item in items:
            ContextItem.model_validate(item)
    except (ValidationError, TypeError) as exc:
        raise ValueError("invalid_context_item") from exc


def _source_reference(value):
    """Persist internal evidence identities, expose only human source references."""
    if isinstance(value, dict):
        internal = {
            "chunk_id",
            "document_id",
            "parent_id",
            "version_target_document_id",
            "version_target_parent_id",
            "retrieval_score",
            "version_relation",
        }
        projected = {
            key: _source_reference(item)
            for key, item in value.items()
            if key not in internal
        }
        if "evidence_refs" in projected:
            projected["notice"] = "历史来源引用，不代表本轮已读取正文或核验法律效力。"
        return projected
    if isinstance(value, list):
        return [_source_reference(item) for item in value]
    return value


def _identity_labels(value):
    labels = {}
    if isinstance(value, dict):
        label = (
            " · ".join(
                str(value.get(k, ""))
                for k in ("document_name", "article_no")
                if value.get(k)
            )
            or "材料来源"
        )
        for key, item in value.items():
            if key in {"chunk_id", "document_id", "parent_id"} and isinstance(
                item, str
            ):
                labels[item] = label
            elif isinstance(item, (dict, list)):
                labels.update(_identity_labels(item))
    elif isinstance(value, list):
        for item in value:
            labels.update(_identity_labels(item))
    return labels
