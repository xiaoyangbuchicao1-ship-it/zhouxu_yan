"""Budget-pressure recovery, independent of the domain and retrieval algorithm."""

import json
from uuid import uuid4

from backend.contracts.modules.loop import PerceivedContext, ReasoningInput
from backend.contracts.modules.perception import ContextAssembly, ContextItem
from backend.contracts.schemas.core import ModuleRequest, PortError


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
            if decision.kind != "answer" or not decision.answer or decision.incomplete:
                raise ValueError("invalid_compaction_answer")
            # A summary that expands its input cannot be a successful recovery.
            if len(decision.answer.encode()) >= len(
                json.dumps(
                    [item.model_dump(mode="json") for item in chosen],
                    ensure_ascii=False,
                ).encode()
            ):
                raise ValueError("expanded_compaction_answer")
            return decision.answer, [item.id for item in chosen]
        except Exception:
            # A compaction model is an optimization.  A provider hiccup or an
            # overlong summary must not turn a recoverable context pressure
            # event into a dead Run.  The deterministic projection retains
            # identities and bounded observations and is explicitly marked as
            # a reference summary so it cannot be mistaken for fresh evidence.
            return self._deterministic_summary(chosen), [item.id for item in chosen]

    def _deterministic_summary(self, items: list[ContextItem]) -> str:
        """Build a bounded, source-preserving fallback without model guesses."""

        budget = self.context.context_budget()
        limit = max(1200, min(12000, budget.target * 2))
        chunks = ["历史上下文摘要（自动整理，仅覆盖所列记录）："]
        used = len(chunks[0])
        for item in items:
            body = item.body if isinstance(item.body, dict) else {}
            payload = {
                "record_id": item.id,
                "source": item.source,
                "kind": item.kind,
                "content": self._bounded(body, max(240, min(1600, limit // 4))),
            }
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if used + len(text) + 1 > limit:
                break
            chunks.append(text)
            used += len(text) + 1
        return "\n".join(chunks)

    @classmethod
    def _bounded(cls, value, limit: int):
        if isinstance(value, str):
            if len(value) <= limit:
                return value
            head = max(80, limit // 2)
            return value[:head] + "…" + value[-max(40, limit - head - 1):]
        if isinstance(value, list):
            result = []
            for item in value:
                if len(json.dumps(result, ensure_ascii=False)) >= limit:
                    break
                result.append(cls._bounded(item, max(80, limit // 3)))
            return result
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                candidate = cls._bounded(item, max(80, limit // 3))
                result[str(key)] = candidate
                if len(json.dumps(result, ensure_ascii=False)) >= limit:
                    break
            return result
        return value
