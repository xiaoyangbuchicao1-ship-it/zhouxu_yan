"""Model-facing research-card tools.

Information cards are evidence candidates retrieved from OpenSearch. Capability
cards are small, read-only method resources loaded from a pinned JSONL file.
Neither tool writes a legal conclusion into the other layer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.config.settings import BACKEND_ROOT, Settings
from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.contracts.modules.research_cards import (
    CapabilityCardReadInput,
    CapabilityCardSelectInput,
    InformationCardReadInput,
    InformationCardSearchInput,
)
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.infrastructure.search.research_cards import (
    InformationCardRetriever,
)


def _project_root() -> Path:
    return BACKEND_ROOT.parent


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (_project_root() / path).resolve()


def _cjk_ngrams(value: str) -> set[str]:
    grams: set[str] = set()
    for run in re.findall(r"[\u3400-\u9fff]+", value.casefold()):
        for size in (2, 3, 4):
            grams.update(
                run[index : index + size] for index in range(len(run) - size + 1)
            )
    grams.update(re.findall(r"[a-z0-9][a-z0-9_-]{1,}", value.casefold()))
    return grams


class CapabilityCardStore:
    def __init__(self, settings: Settings):
        cfg = settings.research_cards
        root = _resolve_path(cfg.capability_dir)
        path = root if root.is_file() else root / cfg.capability_file
        if not path.is_file():
            raise PortError(settings.error("capability_card_unavailable"))
        self.settings = settings
        self.path = path
        self.cards = self._load(path)
        if not self.cards:
            raise PortError(settings.error("capability_card_unavailable"))

    @staticmethod
    def _load(path: Path) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        with path.open(encoding="utf-8-sig") as stream:
            for line_number, raw in enumerate(stream, 1):
                if not raw.strip():
                    continue
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid_capability_card_json:{line_number}"
                    ) from exc
                if not isinstance(value, dict):
                    raise ValueError(f"invalid_capability_card_record:{line_number}")
                card_id = value.get("candidate_id") or value.get("card_id")
                trigger = value.get("trigger")
                name = value.get("name")
                procedure = value.get("steps") or value.get("procedure")
                if not all(
                    isinstance(item, str) and item.strip()
                    for item in (card_id, trigger, name)
                ):
                    raise ValueError(f"invalid_capability_card_identity:{line_number}")
                if not isinstance(procedure, list) or not all(
                    isinstance(item, str) and item.strip() for item in procedure
                ):
                    raise ValueError(f"invalid_capability_card_steps:{line_number}")
                if card_id in result:
                    raise ValueError(f"duplicate_capability_card:{card_id}")
                normalized = dict(value)
                normalized["candidate_id"] = card_id
                normalized["steps"] = list(procedure)
                normalized["evidence_requirements"] = list(
                    value.get("evidence_requirements") or []
                )
                normalized["stop_conditions"] = list(
                    value.get("stop_conditions")
                    or value.get("failure_and_rollback")
                    or []
                )
                normalized["not_applicable"] = str(value.get("not_applicable") or "")
                result[card_id] = normalized
        return result

    def select(self, query: str, limit: int) -> dict:
        query_grams = _cjk_ngrams(query)
        ranked = []
        for card in self.cards.values():
            name_grams = _cjk_ngrams(str(card["name"]))
            trigger_grams = _cjk_ngrams(str(card["trigger"]))
            procedure_grams = _cjk_ngrams(" ".join(card["steps"]))
            name_hits = query_grams & name_grams
            trigger_hits = query_grams & trigger_grams
            procedure_hits = query_grams & procedure_grams
            score = len(name_hits) * 4 + len(trigger_hits) * 2 + len(procedure_hits)
            if score < 2:
                continue
            ranked.append(
                (
                    score,
                    card["candidate_id"],
                    {
                        "candidate_id": card["candidate_id"],
                        "name": card["name"],
                        "trigger": card["trigger"],
                        "score": score,
                        "matched_terms": sorted(name_hits | trigger_hits),
                        "evidence_requirements": card["evidence_requirements"],
                        "stop_conditions": card["stop_conditions"],
                        "not_applicable": card["not_applicable"],
                    },
                )
            )
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return {
            "status": "ok",
            "query": query,
            "items": [item[2] for item in ranked[:limit]],
            "source_type": "capability_card_resource",
            "selected_count": min(limit, len(ranked)),
        }

    def read(self, candidate_id: str) -> dict:
        card = self.cards.get(candidate_id)
        if card is None:
            return {"status": "not_found", "candidate_id": candidate_id}
        return {
            "status": "ok",
            "card": card,
            "source_type": "capability_card_resource",
        }


class InformationCardSearchTool:
    def __init__(self, retriever: InformationCardRetriever, settings: Settings):
        self.retriever, self.settings = retriever, settings

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="information_card_search",
            description=self.settings.research_cards.search_description,
            parameters=InformationCardSearchInput.model_json_schema(),
            read_only=True,
            reuse_identical_results=True,
        )

    async def execute_context(self, request: ModuleRequest) -> ActionResult:
        try:
            value = InformationCardSearchInput.model_validate(request.input.arguments)
            result = await self.retriever.search(
                value.query,
                limit=value.limit,
                evidence_type=value.evidence_type,
                topics=value.topics,
            )
        except PortError:
            raise
        except ValueError:
            raise PortError(self.settings.error("invalid_arguments")) from None
        budget = min(
            self.settings.research_cards.information_result_budget_chars,
            max(0, (request.context or {}).get("tool_result_chars", 1_000_000)),
        )
        result = _pack_information_result(result, budget, self.settings)
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            result=result,
            outcome=(
                "partial"
                if result.get("truncated") or result["status"] == "partial"
                else "empty"
                if not result["items"]
                else "ok"
            ),
            session_summary={
                "query": value.query,
                "card_refs": [item["card_id"] for item in result["items"]],
            },
        )


class InformationCardReadTool:
    def __init__(self, retriever: InformationCardRetriever, settings: Settings):
        self.retriever, self.settings = retriever, settings

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="information_card_read",
            description=self.settings.research_cards.read_description,
            parameters=InformationCardReadInput.model_json_schema(),
            read_only=True,
            reuse_identical_results=True,
        )

    async def execute_context(self, request: ModuleRequest) -> ActionResult:
        try:
            value = InformationCardReadInput.model_validate(request.input.arguments)
            result = await self.retriever.read(value.card_id)
        except PortError:
            raise
        except ValueError:
            raise PortError(self.settings.error("invalid_arguments")) from None
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            result=result,
            outcome="empty" if result.get("status") == "not_found" else "ok",
            session_summary={"card_id": value.card_id, "status": result.get("status")},
        )


class CapabilityCardSelectTool:
    def __init__(self, store: CapabilityCardStore, settings: Settings):
        self.store, self.settings = store, settings

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="capability_card_select",
            description=self.settings.research_cards.capability_select_description,
            parameters=CapabilityCardSelectInput.model_json_schema(),
            read_only=True,
            reuse_identical_results=True,
        )

    async def execute_context(self, request: ModuleRequest) -> ActionResult:
        try:
            value = CapabilityCardSelectInput.model_validate(request.input.arguments)
        except ValueError:
            raise PortError(self.settings.error("invalid_arguments")) from None
        result = self.store.select(value.query, value.limit)
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            result=result,
            outcome="empty" if not result["items"] else "ok",
            session_summary={
                "query": value.query,
                "capability_card_ids": [
                    item["candidate_id"] for item in result["items"]
                ],
            },
        )


class CapabilityCardReadTool:
    def __init__(self, store: CapabilityCardStore, settings: Settings):
        self.store, self.settings = store, settings

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="capability_card_read",
            description=self.settings.research_cards.capability_read_description,
            parameters=CapabilityCardReadInput.model_json_schema(),
            read_only=True,
            reuse_identical_results=True,
        )

    async def execute_context(self, request: ModuleRequest) -> ActionResult:
        try:
            value = CapabilityCardReadInput.model_validate(request.input.arguments)
        except ValueError:
            raise PortError(self.settings.error("invalid_arguments")) from None
        result = self.store.read(value.candidate_id)
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            result=result,
            outcome="empty" if result.get("status") == "not_found" else "ok",
            session_summary={
                "candidate_id": value.candidate_id,
                "status": result.get("status"),
            },
        )


def _pack_information_result(result: dict, budget: int, settings: Settings) -> dict:
    packed = dict(result)
    kept = []
    used = 0
    for item in result.get("items", []):
        serialized = json.dumps(item, ensure_ascii=False)
        if kept and used + len(serialized) > budget:
            break
        if not kept and len(serialized) > budget:
            raise PortError(settings.error("rag_tool_budget"))
        kept.append(item)
        used += len(serialized)
    packed["items"] = kept
    packed["truncated"] = len(kept) < len(result.get("items", []))
    return packed
