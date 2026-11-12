"""Deterministic selection measured as complete provider requests."""

from dataclasses import dataclass
from time import perf_counter


@dataclass
class SelectionResult:
    items: list
    omitted_item_ids: list[str]
    decisions: list[dict]
    used_tokens: int
    compacted: bool


class ContextBudgetExceeded(OverflowError):
    def __init__(self, *, used_tokens, initial_tokens, decisions, items):
        super().__init__("required_complete_request_over_budget")
        self.used_tokens = used_tokens
        self.initial_tokens = initial_tokens
        self.decisions = decisions
        self.items = items


def select_context(items, budget, measure, required_item_ids, policy):
    original = items
    items = [item.model_copy(deep=True) for item in items]
    initial = measure(items).tokens
    if initial <= budget.warning:
        return SelectionResult(items, [], [], initial, False)
    began = perf_counter()
    decisions = []
    # Lossless projections first. History is never deleted here: the Harness
    # must commit a summary before replacing the exchanges covered by it.
    for attempt in range(policy.max_compactions):
        before = measure(items).tokens
        if before <= budget.low:
            break
        if perf_counter() - began > policy.compaction_seconds:
            break
        if attempt == 0:
            # A reliable material reader exists. Keep the attributed entry point
            # and actual coverage; remove only initial duplicate reading bodies.
            for item in items:
                if item.type != "resource" or not item.body.get("material_id"):
                    continue
                body = item.body
                if body.get("units"):
                    item.body = {
                        k: v
                        for k, v in body.items()
                        if k not in {"units", "coverage", "read_hint"}
                    }
                    item.body.update(
                        {
                            "read_hint": "正文未内联，使用 read_material mode=read 取得正文；不能把引用当成已读证据。",
                            "next_cursor": None,
                        }
                    )
                    decisions.append(
                        {
                            "id": item.id,
                            "expression": "readable_reference",
                            "reason": "above_warning",
                        }
                    )
        else:
            for item in items:
                resource_id = item.body.get("resource_id")
                if item.type != "tool_result" or not resource_id:
                    continue
                result = item.body.get("result", {})
                if isinstance(result, dict) and result.get("body_in_resource"):
                    continue
                # Accumulation needs a committed semantic summary. Turning all
                # normal results into bare references loses the facts just read.
                # Only an individually unmanageable result needs paging first.
                if measure([item]).tokens <= budget.target:
                    continue
                item.body["result"] = {
                    "status": result.get("status", "ok") if isinstance(result, dict) else "ok",
                    "material_id": resource_id, "body_in_resource": True,
                    "read_hint": "工具结果正文已保存，使用 read_material 读取；此回执不代表正文已读。",
                }
                decisions.append({"id": item.id, "expression": "readable_tool_result", "reason": "above_warning"})
        after = measure(items).tokens
        if after < before:
            decisions.append(
                {"pass": attempt + 1, "before_tokens": before, "after_tokens": after}
            )
        if after >= before and attempt > 0:
            break
    used = measure(items).tokens
    if used > budget.warning or used > budget.hard:
        raise ContextBudgetExceeded(
            used_tokens=used, initial_tokens=initial, decisions=decisions, items=items
        )
    ids = {item.id for item in items}
    return SelectionResult(
        items, [i.id for i in original if i.id not in ids], decisions, used, True
    )
