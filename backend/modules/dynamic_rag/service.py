"""Retrieve -> assess missing evidence -> retrieve again, within explicit budgets."""

import asyncio
import json
from time import perf_counter

from backend.config.settings import Settings
from backend.contracts.modules.dynamic_rag import (
    Evidence,
    RagInput,
    RagOutput,
    RetrievalStep,
)
from backend.contracts.ports import EvidenceReviewPort, SearchPort
from backend.contracts.schemas.core import ModuleRequest, ModuleResponse, PortError
from backend.modules.dynamic_rag.query_expansion import expand_query
from backend.modules.dynamic_rag.query_plan import (
    changes_location,
    followup_plan,
    prepare_query,
)


def _pack(
    items: list[Evidence], limit: int, budget: int
) -> tuple[list[Evidence], list[str]]:
    """Keep original chunks intact; never manufacture a readable-reference promise."""
    kept, omitted, used = [], [], 0
    for item in items:
        cost = len(item.model_dump_json())
        if len(kept) < limit and used + cost <= budget:
            kept.append(item.model_copy(deep=True))
            used += cost
        else:
            omitted.append(item.chunk_id)
    return kept, omitted


class DynamicRag:
    def __init__(
        self,
        search: SearchPort,
        settings: Settings,
        reviewer: EvidenceReviewPort | None = None,
    ):
        self.search, self.settings, self.reviewer = search, settings, reviewer

    async def handle(
        self, request: ModuleRequest[RagInput]
    ) -> ModuleResponse[RagOutput]:
        cfg = self.settings.rag
        value = request.input
        query = value.query.strip()
        # Search wording may be inferred by the model. Sufficiency must still
        # answer the user's request, not expand to every term in that wording.
        objective = (request.context or {}).get("user_request")
        objective = (
            objective
            if isinstance(objective, str) and objective.strip()
            else value.query
        )
        context = request.context or {}
        objective_data = {
            "request": objective,
            "user_supplied_facts": context.get("user_facts", {}),
            "constraints": context.get("user_constraints", []),
        }
        if context.get("conversation_context"):
            objective_data["conversation_context"] = context["conversation_context"]
        if not query or len(query) > cfg.max_query_chars:
            return ModuleResponse(
                status="needs_input", error=self.settings.error("rag_invalid_query")
            )
        limit = min(value.limit or cfg.default_limit, cfg.max_limit)
        initial_plan = prepare_query(value, cfg)
        objective_data["retrieval_task"] = initial_plan.model_dump(
            include={"query", "document_name", "article_nos", "version_scope"}
        )
        objective = json.dumps(objective_data, ensure_ascii=False)
        start = perf_counter()
        pool: dict[str, Evidence] = {}
        selected: list[str] | None = None
        steps, notices, missing, previous = [], [], [], []
        expansions_used = 0
        sufficiency = "unassessed"
        stop = "round_limit"
        status, error = "ok", None

        try:
            async with asyncio.timeout(cfg.total_timeout_seconds):
                for round_no in range(1, cfg.max_rounds + 1):
                    plan = initial_plan if round_no == 1 else followup_plan(query, initial_plan, cfg)
                    batch = await self.search.search(
                        plan, value.filters.model_copy(deep=True), limit
                    )
                    previous.append(query)
                    notices.extend(batch.notices)
                    before = len(pool)
                    for item in batch.items:
                        # IDs are corpus identities; contradictory retransmissions
                        # must not silently replace the original evidence.
                        old = pool.get(item.chunk_id)
                        if old and old.model_dump(
                            exclude={"retrieval_score"}
                        ) != item.model_dump(exclude={"retrieval_score"}):
                            raise PortError(self.settings.error("rag_invalid_evidence"))
                        pool.setdefault(item.chunk_id, item.model_copy(deep=True))
                    step = RetrievalStep(
                        round=round_no,
                        query=query,
                        method=batch.method,
                        found_ids=[item.chunk_id for item in batch.items],
                        new_count=len(pool) - before,
                        embedding_seconds=batch.embedding_seconds,
                        search_seconds=batch.search_seconds,
                        plan=plan,
                    )
                    steps.append(step)
                    if self.reviewer is None:
                        stop = "single_pass"
                        break
                    visible, hidden = _pack(
                        list(pool.values()), len(pool), cfg.review_budget_chars
                    )
                    if hidden:
                        notices.append("review_budget_omitted_evidence")
                    began_review = perf_counter()
                    try:
                        review = await self.reviewer.review(
                            objective,
                            visible,
                            list(previous),
                            value.filters.model_copy(deep=True),
                        )
                    except PortError:
                        stop = "review_unavailable"
                        notices.append("review_unavailable")
                        sufficiency = "unassessed"
                        selected = None
                        break
                    finally:
                        step.review_seconds = perf_counter() - began_review
                    known = {item.chunk_id for item in visible}
                    invalid_ids = any(item not in known for item in review.selected_ids)
                    selected = list(
                        dict.fromkeys(
                            item for item in review.selected_ids if item in known
                        )
                    )
                    missing = list(review.missing_aspects)
                    step.decision, step.reason = review.decision, review.reason
                    step.missing_aspects, step.review_tokens = (
                        missing,
                        review.total_tokens,
                    )
                    if invalid_ids:
                        notices.append("review_unknown_ids_ignored")
                    if review.decision == "sufficient":
                        if not selected or invalid_ids:
                            stop = "invalid_review_selection"
                            sufficiency = "unassessed"
                            selected = None
                        else:
                            sufficiency, stop = "sufficient", "sufficient"
                        break
                    sufficiency = "insufficient"
                    if review.decision == "insufficient":
                        stop = "insufficient"
                        break
                    followup = (review.next_query or "").strip()
                    normalized_previous = {
                        "".join(old.split()).casefold() for old in previous
                    }
                    followup_valid = bool(followup) and len(followup) <= cfg.max_query_chars
                    if followup_valid and changes_location(followup, initial_plan, cfg):
                        stop = "scope_change_required"
                        notices.append("followup_outside_location_scope")
                        # A reviewer may ask to leave this scope without selecting
                        # its valid results; keep them available for the main model.
                        selected = selected or None
                        break
                    followup_repeated = (
                        followup_valid
                        and "".join(followup.split()).casefold() in normalized_previous
                    )
                    if followup_valid and not followup_repeated:
                        query = followup
                        continue
                    if expansions_used < cfg.max_query_expansions and round_no < cfg.max_rounds:
                        expanded = expand_query(
                            value.query,
                            query,
                            missing,
                            previous,
                            noise_terms=tuple(cfg.query_noise_terms),
                            anchor_limit=cfg.content_anchor_limit,
                            anchor_max_chars=cfg.content_anchor_max_chars,
                            max_chars=cfg.max_query_chars,
                        )
                        if expanded:
                            expansions_used += 1
                            notices.append("query_expansion_applied")
                            query = expanded
                            continue
                    if not followup_valid:
                        stop = "invalid_followup"
                    elif followup_repeated:
                        stop = "repeated_query"
                    else:
                        stop = "no_new_evidence" if round_no > 1 and len(pool) == before else "round_limit"
                    break
        except TimeoutError:
            status, stop = "failed", "timeout"
            error = self.settings.error("rag_timeout", retryable=True)
            sufficiency = "unassessed"
            selected = None
        except PortError as exc:
            status, stop, error = "failed", "retrieval_failed", exc.error
            sufficiency = "unassessed"
            selected = None
        except asyncio.CancelledError:
            raise
        except Exception:
            status, stop = "failed", "retrieval_failed"
            error = self.settings.error("rag_search_failed")
            sufficiency = "unassessed"
            selected = None

        ordered = (
            list(pool.values()) if selected is None else [pool[key] for key in selected]
        )
        items, omitted = _pack(ordered, limit, cfg.evidence_budget_chars)
        kept_ids = {item.chunk_id for item in items}
        omitted = list(
            dict.fromkeys(omitted + [key for key in pool if key not in kept_ids])
        )
        if sufficiency == "sufficient" and any(
            key not in kept_ids for key in (selected or [])
        ):
            sufficiency = "unassessed"
            notices.append("selected_evidence_exceeds_output_budget")
        return ModuleResponse(
            status=status,
            error=error,
            output=RagOutput(
                items=items,
                stop_reason=stop,
                sufficiency=sufficiency,
                missing_aspects=missing,
                notices=list(dict.fromkeys(notices)),
                steps=steps,
                elapsed_seconds=perf_counter() - start,
                omitted_ids=omitted,
            ),
        )
