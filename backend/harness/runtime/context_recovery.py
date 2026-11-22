"""Budget-pressure recovery, independent of the domain and retrieval algorithm."""

import json
import logging
from uuid import uuid4

from backend.contracts.modules.loop import PerceivedContext, ReasoningInput
from backend.contracts.modules.perception import ContextAssembly, ContextItem
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.infrastructure.model.compaction import compaction_incomplete_reason


def compaction_succeeded(summary: str) -> bool:
    """Return whether a recovery result is a real semantic summary.

    Reference-only fallbacks deliberately keep the complete originals but do
    not claim that their facts were condensed. The UI must not announce those
    fallbacks as a successful compaction.
    """
    try:
        payload = json.loads(summary)
    except json.JSONDecodeError:
        return True
    return not (
        isinstance(payload, dict) and payload.get("status") == "reference_only"
    )


def exchange_groups(items):
    groups = {}
    for item in items:
        key = next(
            (link.target_id for link in item.links if link.relation == "responds_to"),
            item.id,
        )
        groups.setdefault(key, []).append(item)
    return list(groups.values())


class ContextRecovery:
    def __init__(self, model, context, settings, catalog):
        self.model, self.context, self.settings = model, context, settings
        self.catalog = catalog

    async def summarize(self, perceived, *, run_id):
        """Summarize a measured prefix of complete, unprotected exchanges.

        Returns text AND exact coverage. The caller commits both before it
        removes records from the working view. Originals remain in checkpoints.
        """
        protected = set(perceived.assembly.required_item_ids)
        groups = [
            group
            for group in exchange_groups(perceived.items)
            if not any(
                item.id in protected or item.type == "resource" for item in group
            )
        ]
        chosen = []

        def envelope(items):
            return ContextItem(
                id="summary-input",
                type="message",
                source="memory",
                kind="history.data",
                role="user",
                body={
                    "current_task": [item.body for item in perceived.items if item.id == "current-user"],
                    "summary_scope": {
                        "covered_record_ids": [item.id for item in items],
                        "other_records_remain_in_working_context": True,
                    },
                    "records_to_summarize": [i.model_dump(mode="json") for i in items],
                },
            )

        budget = self.context.context_budget()
        for group in groups:
            proposed = chosen + group
            estimate = self.context.estimate_context(
                [envelope(proposed)], [], run_id=run_id
            )
            if estimate.tokens > budget.target:
                break
            chosen = proposed
        if not chosen:
            if groups:
                # One historical exchange can itself exceed a model request.
                # Preserve a readable original, explicitly WITHOUT claiming
                # it was semantically summarized or its facts were retained.
                ids = [item.id for item in groups[0]]
                key = "context:" + ids[0]
                self.catalog.bind_text(
                    key,
                    "历史原始记录",
                    json.dumps(
                        [item.model_dump(mode="json") for item in groups[0]],
                        ensure_ascii=False,
                    ),
                    run_id=run_id,
                )
                return json.dumps(
                    {
                        "material_id": key,
                        "covered_record_ids": ids,
                        "status": "reference_only",
                        "notice": "历史记录过长，正文尚未在此摘要中概括；需要时用 read_material 读取原始记录。",
                    },
                    ensure_ascii=False,
                ), ids
            raise PortError(self.settings.error("context_recovery_exhausted"))
        try:
            decision = await self.model.decide(
                ModuleRequest(
                    run_id=run_id,
                    call_id=uuid4().hex,
                    input=ReasoningInput(
                        purpose="compaction",
                        perceived=PerceivedContext(
                            run_id=run_id,
                            items=[envelope(chosen)],
                            assembly=ContextAssembly(),
                        ),
                    ),
                )
            )
            if (
                decision.kind != "answer"
                or not decision.answer
                or decision.incomplete
                or decision.finish_reason in {
                    "length",
                    "max_tokens",
                    "content_filter",
                    "refusal",
                    "sensitive",
                }
            ):
                return self._reference(chosen, run_id, "invalid_compaction_answer")
            incomplete_reason = compaction_incomplete_reason(decision.finish_reason)
            if incomplete_reason:
                return self._reference(chosen, run_id, incomplete_reason)
            # A summary that expands its input cannot be a successful recovery.
            if len(decision.answer.encode()) >= len(
                json.dumps(
                    [item.model_dump(mode="json") for item in chosen],
                    ensure_ascii=False,
                ).encode()
            ):
                return self._reference(chosen, run_id, "expanded_compaction_answer")
            return decision.answer, [item.id for item in chosen]
        except PortError as exc:
            logging.getLogger(__name__).warning("compaction unavailable run=%s code=%s", run_id, exc.error.code)
            return self._reference(chosen, run_id, exc.error.code)

    def _reference(self, items, run_id, reason):
        """Retain every original record instead of claiming a truncated summary."""
        ids = [item.id for item in items]
        key = "context:" + ids[0]
        self.catalog.bind_text(
            key, "历史原始记录",
            json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False),
            run_id=run_id,
        )
        return json.dumps({
            "material_id": key, "covered_record_ids": ids,
            "status": "reference_only", "reason": reason,
            "notice": "摘要未完成，所列原始记录已完整保留；需要时用 read_material 回读，不能视为事实已被概括。",
        }, ensure_ascii=False), ids
