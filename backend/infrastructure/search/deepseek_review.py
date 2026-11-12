"""Bounded evidence assessment using the existing authenticated model transport."""

import json

import httpx
from pydantic import ValidationError

from backend.contracts.modules.dynamic_rag import (
    Evidence,
    EvidenceReview,
    RetrievalFilters,
)
from backend.contracts.schemas.core import PortError
from backend.infrastructure.model.deepseek import DeepSeekModel


class DeepSeekEvidenceReviewer:
    def __init__(self, model: DeepSeekModel):
        self.model = model

    async def review(
        self, query: str, evidence: list[Evidence], previous_queries: list[str],
        filters: RetrievalFilters,
    ) -> EvidenceReview:
        settings = self.model.settings
        try:
            response = await self.model.client.post(
                settings.model.endpoint,
                json={
                    "model": settings.model.name,
                    "messages": [
                        {"role": "system", "content": settings.rag.review_prompt},
                        {"role": "user", "content": json.dumps({
                            "original_query": query, "filters": filters.model_dump(),
                            "previous_queries": previous_queries,
                            "evidence": [item.model_dump(exclude={"retrieval_score"}) for item in evidence],
                        }, ensure_ascii=False)},
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": settings.rag.review_max_tokens,
                    "temperature": settings.model.temperature,
                    "thinking": {"type": "disabled"},
                },
            )
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ValueError("incomplete_review")
            review = EvidenceReview.model_validate_json(choice["message"]["content"])
            usage = data.get("usage") or {}
            tokens = usage.get("total_tokens")
            review.total_tokens = tokens if isinstance(tokens, int) else None
            return review
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, ValidationError):
            raise PortError(settings.error("rag_review_failed")) from None
