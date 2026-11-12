"""An index of selected, actually visible context; no semantic task classifier."""

from collections import defaultdict

from backend.contracts.modules.perception import ContextItem


def covered_length(intervals):
    end, length = 0, 0
    for start, stop in sorted(intervals):
        if stop > end:
            length += stop - max(start, end)
            end = stop
    return length


def organize_context(items, policy):
    """Recompute after each selection/projection, including budget sizing probes.

    A read ledger, reference or committed summary cannot contribute body coverage.
    Only ranges backed by text still present in these items are counted.
    """
    items = [i for i in items if i.id != "context-inventory"]
    if not policy.inventory_enabled:
        return items
    current = next((i for i in items if i.id == "current-user"), None)
    if current is None:
        return items
    documents = {}
    for item in items:
        if item.type == "resource" and item.status in {
            "available",
            "partial",
            "failed",
            "unavailable",
        }:
            body = item.body
            if body.get("material_id"):
                documents[body["material_id"]] = body
    spans = defaultdict(list)
    locations = defaultdict(list)
    operations = []
    for item in items:
        if item.status not in {"available", "partial"}:
            continue
        if item.type == "tool_result":
            action = item.body
            observation = {
                "tool_call_id": action.get("action_id"),
                "tool": action.get("tool_name"),
                "outcome": action.get("outcome", "ok"),
                "error_code": (action.get("error") or {}).get("code"),
            }
            body = action.get("result")
            if isinstance(body, dict) and "query" in body:
                observation["query"] = body["query"]
                observation["returned_source_labels"] = [
                    e["source_label"]
                    for e in body.get("items", [])
                    if isinstance(e, dict) and e.get("source_label")
                ][: policy.inventory_max_sources]
            operations.append(observation)
        elif item.type == "resource":
            body = item.body
        else:
            continue
        if (
            not isinstance(body, dict)
            or not body.get("units")
            or body.get("body_in_resource")
        ):
            continue
        key = body.get("material_id")
        source = documents.get(key)
        if source is None or body.get("parse_version") != source.get("parse_version"):
            continue
        units = {u.get("unit_id"): u for u in body["units"] if isinstance(u, dict)}
        provided = []
        for span in body.get("coverage", []):
            unit = units.get(span.get("unit_id"))
            start, stop = span.get("char_start"), span.get("char_end")
            if (
                unit is None
                or not isinstance(start, int)
                or not isinstance(stop, int)
                or start < 0
                or stop <= start
                or stop - start > len(unit.get("text", ""))
            ):
                continue
            spans[(key, span["unit_id"])].append((start, stop))
            provided.append(span)
        if provided:
            locations[key].append(
                {
                    "kind": item.type,
                    "tool_call_id": item.body.get("action_id")
                    if item.type == "tool_result"
                    else None,
                    "start": provided[0].get("source") or provided[0].get("unit_id"),
                    "end": provided[-1].get("source") or provided[-1].get("unit_id"),
                }
            )
    materials = []
    for key, body in documents.items():
        total = body.get("text_char_count")
        visible = sum(
            covered_length(value)
            for (material, _), value in spans.items()
            if material == key
        )
        full = isinstance(total, int) and total > 0 and visible == total
        materials.append(
            {
                "material_id": key,
                "name": body.get("name"),
                "parse_status": body.get("parse_status"),
                "parse_complete": body.get("complete", False),
                "total_parsed_text_chars": total,
                "visible_text_chars": visible,
                "full_text_visible": full,
                "unprovided_text_chars": max(0, total - visible)
                if isinstance(total, int)
                else None,
                "missing_parse_ranges": body.get("missing_ranges", []),
                "visual_content_present": body.get("visual_content_present", False),
                "body_locations": locations[key][-policy.inventory_max_sources :],
            }
        )
    goal = current.body.get("goal", "")
    continuation = next(
        (
            i.body
            for i in items
            if i.id == "task-continuation-state" and i.source == "harness"
        ),
        {},
    )
    index = ContextItem(
        id="context-inventory",
        type="message",
        source="harness",
        role="user",
        kind="context.inventory",
        body={
            "materials": materials,
            "observations_count": len(operations),
            "recent_observations": operations[-policy.inventory_max_operations :],
            "already_delivered_chars": continuation.get("already_delivered_chars", 0),
            "remaining_work": continuation.get("remaining_work", []),
            "task_completion": "not_asserted",
            "current_goal": goal[: policy.inventory_goal_chars],
            "goal_is_excerpt": len(goal) > policy.inventory_goal_chars,
            "goal_location": "current_user_request",
            "latest_response_location": "latest_user_response"
            if (current.body.get("facts") or {}).get("user_answers")
            else None,
        },
    )
    return items + [index]
