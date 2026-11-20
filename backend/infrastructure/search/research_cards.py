"""Read-only access to the independent information-card index.

The HTTP client and pinned query encoder are shared with the existing RAG
lifetime. The existing vector query and RRF functions are reused while law,
case and academic information remain separate evidence sources.
"""

from __future__ import annotations

import asyncio
import urllib.parse

import httpx
from opensearch.opensearch_delivery.scripts.maritime_index import rrf, vector_body

from backend.config.settings import Settings
from backend.contracts.schemas.core import PortError


class InformationCardRetriever:
    def __init__(self, client: httpx.AsyncClient, settings: Settings, encoder=None):
        self.client = client
        self.settings = settings
        self.encoder = encoder
        self._validated = False
        alias = settings.research_cards.information_alias
        if not alias.replace("_", "").replace("-", "").isalnum():
            raise PortError(settings.error("information_card_configuration"))

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = await self.client.request(method, path, **kwargs)
            if response.status_code == 404:
                payload = response.json()
                # A missing document is an ordinary empty read. A missing
                # index is a deployment error, never an empty search result.
                if isinstance(payload, dict) and payload.get("found") is False:
                    return {"status": "not_found"}
                raise PortError(self.settings.error("information_card_configuration"))
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise PortError(
                self.settings.error("information_card_search_failed", retryable=True)
            ) from exc
        if not isinstance(payload, dict):
            raise PortError(self.settings.error("information_card_search_failed"))
        return payload

    async def validate_index(self):
        alias = self.settings.research_cards.information_alias
        data = await self._request("GET", f"/{alias}/_mapping")
        cfg = self.settings.rag
        expected = {
            "model": cfg.embedding_model,
            "revision": cfg.embedding_revision,
            "pooling": "cls",
            "normalize": True,
            "dimensions": cfg.embedding_dimensions,
        }
        if (
            len(data) != 1
            or next(iter(data.values()))["mappings"].get("_meta", {}).get("embedding")
            != expected
        ):
            raise PortError(self.settings.error("information_card_configuration"))
        self._validated = True

    async def _hits(self, body):
        data = await self._request(
            "POST",
            f"/{self.settings.research_cards.information_alias}/_search",
            json=body,
        )
        if data.get("timed_out") or data.get("_shards", {}).get("failed", 0):
            raise PortError(
                self.settings.error("information_card_search_failed", retryable=True)
            )
        hits = data["hits"]["hits"]
        if not isinstance(hits, list) or any(
            not isinstance(hit.get("_source"), dict)
            or not hit["_source"].get("card_id")
            for hit in hits
        ):
            raise PortError(self.settings.error("information_card_search_failed"))
        return hits, data["hits"].get("total", {}).get("value", len(hits))

    async def search(
        self,
        query: str,
        *,
        limit: int,
        evidence_type: str | None = None,
        topics: list[str] | None = None,
    ) -> dict:
        if not self._validated:
            await self.validate_index()
        filters = []
        if evidence_type:
            filters.append({"term": {"evidence_type": evidence_type}})
        if topics:
            filters.append({"terms": {"topics": topics}})
        body = {
            "size": self.settings.rag.candidates,
            "track_total_hits": True,
            "_source": {"excludes": ["claim_or_fact", "vector", "source_provenance"]},
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": [
                                    "retrieval_text^5",
                                    "claim_or_fact^3",
                                    "source_title^2",
                                    "caveat",
                                ],
                                "type": "best_fields",
                                "operator": "or",
                            }
                        }
                    ],
                    "filter": filters,
                }
            },
        }

        async def semantic_hits():
            if self.encoder is None:
                raise PortError(self.settings.error("rag_embedding_failed"))
            vector = await self.encoder.encode(query)
            semantic = vector_body(vector, filters, self.settings.rag.candidates)
            semantic["_source"] = body["_source"]
            return await self._hits(semantic)

        lexical, semantic = await asyncio.gather(
            self._hits(body), semantic_hits(), return_exceptions=True
        )
        if isinstance(lexical, BaseException):
            raise lexical
        notices = []
        if isinstance(semantic, BaseException):
            if not isinstance(semantic, (PortError, httpx.HTTPError, ValueError)):
                raise semantic
            notices.append("semantic_retrieval_unavailable_bm25_only")
            vector_hits = []
        else:
            vector_hits = semantic[0]
        rows = rrf(
            lexical[0],
            vector_hits,
            size=limit,
            k=self.settings.rag.rrf_k,
            identity_field="card_id",
        )
        items = [
            {
                **row["chunk"],
                "retrieval_score": row["rrf_score"],
                "lane_ranks": {
                    lane: row[lane + "_rank"]
                    for lane in ("bm25", "vector")
                    if lane + "_rank" in row
                },
            }
            for row in rows
        ]
        return {
            "status": "partial" if notices else "ok",
            "query": query,
            "items": items,
            "total": lexical[1],
            "total_scope": "bm25_matches",
            "method": "bm25" if notices else "hybrid",
            "notices": notices,
            "source_type": "validated_information_card",
            "read_hint": "用 information_card_read(card_id) 读取完整卡片；source_path 仅供来源定位，不是工作区文件。",
        }

    async def read(self, card_id: str) -> dict:
        encoded = urllib.parse.quote(card_id, safe="")
        data = await self._request(
            "GET", f"/{self.settings.research_cards.information_alias}/_doc/{encoded}"
        )
        if data.get("status") == "not_found" or not data.get("found", True):
            return {"status": "not_found", "card_id": card_id}
        source = data.get("_source")
        if not isinstance(source, dict) or source.get("card_id") != card_id:
            raise PortError(self.settings.error("information_card_read_failed"))
        source.pop("vector", None)
        return {
            "status": "ok",
            "card": source,
            "source_type": "validated_information_card",
            "read_hint": "已返回完整卡片记录；source_path 仅供原文来源定位，当前工作区不含该原文。",
        }


class UnavailableInformationCardRetriever:
    """Truthful adapter used when OpenSearch is unavailable at API startup."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, *args, **kwargs):
        raise PortError(
            self.settings.error("information_card_search_failed", retryable=True)
        )

    async def read(self, *args, **kwargs):
        raise PortError(self.settings.error("information_card_read_failed"))
