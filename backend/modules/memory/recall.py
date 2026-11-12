from __future__ import annotations

import json

from backend.contracts.modules.loop import ActionResult, DecisionProposal
from backend.contracts.modules.memory import MemoryRecall, MemoryRecord, TaskState

PROTECTED_RECORD_KINDS = ("correction", "constraint", "unresolved")


class DeterministicRecallPolicy:
    """Protect explicit dependencies, then choose recent complete units."""

    def __init__(self, required_kinds=PROTECTED_RECORD_KINDS):
        self.required_kinds = required_kinds

    def select(
        self,
        records: list[MemoryRecord],
        *,
        session_id: str,
        run_id: str,
        task_id: str | None,
        task_state: TaskState | None,
        reference_ids: list[str],
        max_items: int | None,
        max_chars: int,
    ) -> MemoryRecall:
        ordered = sorted(records, key=lambda r: (r.created_at, r.record_id))
        # A run can contain a large tool exchange.  Keeping every record from
        # the run as "required" makes a large attachment or tool response turn
        # into a false user clarification request.  Protect the current user
        # input and explicit/protocol dependencies; complete older exchanges
        # remain recent candidates and can be omitted by the character budget.
        required = {
            r.record_id
            for r in ordered
            if (
                r.run_id == run_id
                and r.source == "user"
            )
            or (
                r.kind in self.required_kinds
                and r.status == "available"
                and (r.task_id is None or r.task_id == task_id)
            )
        }
        required.update(reference_ids)
        units, invalid = self._units(ordered)
        # An unfinished exchange in the active run is a protocol dependency:
        # silently dropping its call/result would make the next model turn
        # ambiguous.  Historical unfinished exchanges remain optional and may
        # be omitted as background noise.
        for unit in units:
            if any(
                r.run_id == run_id and r.record_id in invalid for r in unit
            ):
                required.update(r.record_id for r in unit)
        for unit in units:
            if any(r.record_id in required for r in unit):
                required.update(r.record_id for r in unit)
        ranked = sorted(
            enumerate(units),
            key=lambda pair: (
                not any(r.record_id in required for r in pair[1]),
                not any(r.run_id == run_id for r in pair[1]),
                -pair[0],
            ),
        )
        selected_ids: set[str] = set()
        omitted: dict[str, str] = {}
        task_cost = len(task_state.model_dump_json()) if task_state else 0
        used = task_cost
        for _, unit in ranked:
            reason = next(
                (invalid[r.record_id] for r in unit if r.record_id in invalid), None
            )
            cost = sum(self._cost(r) for r in unit)
            if (
                reason is None
                and max_items is not None
                and len(selected_ids) + len(unit) > max_items
            ):
                reason = "item_budget"
            if reason is None and used + cost > max_chars:
                reason = "character_budget"
            if reason:
                omitted.update({r.record_id: reason for r in unit})
            else:
                selected_ids.update(r.record_id for r in unit)
                used += cost
        missing = required - selected_ids
        known = {r.record_id for r in ordered}
        omitted.update({i: "reference_missing" for i in missing if i not in known})
        blocked = None
        if missing:
            # Missing references and incomplete protocol exchanges are genuine
            # dependencies.  Budget-only omissions are recoverable and must
            # never be surfaced as a request for more user input.
            if any(
                i not in known
                or invalid.get(i) == "incomplete_tool_exchange"
                or omitted.get(i) == "reference_missing"
                for i in missing
            ):
                blocked = "required_context_unavailable"
        if task_cost > max_chars and blocked is None:
            # The task state itself cannot fit.  Keep the diagnostic marker so
            # perception can fail safely without entering waiting_user.
            blocked = "required_context_budget"
        # Keep exchange records adjacent, even if an unrelated record was stored
        # between the call and its results. Unit order stays chronological.
        selected = [r for unit in units for r in unit if r.record_id in selected_ids]
        notices = ["records_omitted"] if omitted else []
        if any(
            omitted.get(i) == "character_budget"
            for i in required
        ):
            notices.append("required_context_budget_recovered")
        return MemoryRecall(
            scope=run_id,
            session_id=session_id,
            run_id=run_id,
            task_id=task_id,
            task_state=task_state.model_copy(deep=True) if task_state else None,
            items=[
                r.model_copy(update={"updates": r.updates or r.body}, deep=True)
                for r in selected
            ],
            records=selected,
            selected_item_ids=[r.record_id for r in selected],
            # Only selected IDs are mandatory for perception.  The complete
            # dependency set remains in trace for diagnosis; this prevents a
            # budget-omitted optional record from re-triggering selection.
            required_item_ids=sorted(required & selected_ids),
            omitted_item_ids=list(omitted),
            omitted_reasons=omitted,
            blocked_reason=blocked,
            budget_chars=max_chars,
            used_chars=used,
            notices=notices,
            trace={
                "candidate_ids": [r.record_id for r in ordered],
                "task_revision": task_state.revision if task_state else None,
                "selected_reasons": {
                    r.record_id: (
                        "current_run"
                        if r.run_id == run_id
                        else "explicit_reference"
                        if r.record_id in reference_ids
                        else "protected_dependency"
                        if r.record_id in required
                        else "recent_background"
                    )
                    for r in selected
                },
                "task_chars": task_cost,
                "unit_count": len(units),
                "protected_item_ids": sorted(required),
                "budget_omitted_required": sorted(
                    i for i in required if omitted.get(i) == "character_budget"
                ),
            },
        )

    @staticmethod
    def _units(records):
        """Group by run + action ID, validate using the existing tool contracts."""
        calls = {}
        owners = {}
        results = {}
        invalid = {}
        for record in records:
            proposal_data = record.payload.get("proposal", {})
            if (
                record.source == "reasoning"
                and isinstance(proposal_data, dict)
                and proposal_data.get("kind") == "action"
            ):
                try:
                    proposal = DecisionProposal.model_validate(proposal_data)
                    requests = proposal.action_requests
                    if not requests or len({a.action_id for a in requests}) != len(
                        requests
                    ):
                        raise ValueError("invalid_calls")
                    calls[record.record_id] = requests
                    for action in requests:
                        key = (record.run_id, action.action_id)
                        if key in owners:
                            invalid[owners[key]] = "incomplete_tool_exchange"
                            invalid[record.record_id] = "incomplete_tool_exchange"
                        owners[key] = record.record_id
                except (ValueError, TypeError, KeyError):
                    invalid[record.record_id] = "incomplete_tool_exchange"
        groups = {r.record_id: [] for r in records}
        for record in records:
            owner = record.record_id
            if record.source == "action":
                try:
                    result = ActionResult.model_validate(record.payload["action"])
                    key = (record.run_id, result.action_id)
                    owner = owners.get(key, record.record_id)
                    expected = next(
                        (
                            a
                            for a in calls.get(owner, [])
                            if a.action_id == result.action_id
                        ),
                        None,
                    )
                    explicit = [
                        link.target_id
                        for link in record.links
                        if link.relation == "responds_to"
                    ]
                    if (
                        expected is None
                        or expected.tool_name != result.tool_name
                        or key in results
                        or (explicit and explicit != [owner])
                    ):
                        raise ValueError("unmatched_result")
                    results[key] = record
                except (ValueError, TypeError, KeyError):
                    invalid[owner] = invalid[record.record_id] = (
                        "incomplete_tool_exchange"
                    )
            groups[owner].append(record)
        for owner, requests in calls.items():
            unit = groups[owner]
            # Wall-clock timestamps can tie. Keep tool results in request order,
            # rather than depending on random record IDs or arrival order.
            result_order = {
                action.action_id: index for index, action in enumerate(requests)
            }
            unit.sort(
                key=lambda r: (
                    -1
                    if r.record_id == owner
                    else result_order.get(
                        r.payload.get("action", {}).get("action_id"), len(requests)
                    )
                )
            )
            call_record = next((r for r in unit if r.record_id == owner), None)
            if call_record is None or any(
                (call_record.run_id, a.action_id) not in results for a in requests
            ):
                invalid[owner] = "incomplete_tool_exchange"
            if any(r.status != "available" for r in unit):
                invalid[owner] = "incomplete_tool_exchange"
        return [unit for unit in groups.values() if unit], invalid

    @staticmethod
    def _cost(record: MemoryRecord) -> int:
        return len(json.dumps(record.model_dump(mode="json"), ensure_ascii=False))
