"""Read-only application adapter; reuse the delivered query/fusion functions."""

import asyncio
import re
import ssl
from pathlib import Path
from time import perf_counter

import httpx
from dotenv import dotenv_values
from opensearch.opensearch_delivery.scripts.maritime_index import (
    bm25_body,
    rrf,
    vector_body,
)
from pydantic import SecretStr, ValidationError

from backend.config.settings import BACKEND_ROOT, Settings
from backend.contracts.modules.dynamic_rag import (
    Evidence,
    RagQueryPlan,
    RetrievalFilters,
    SearchBatch,
    SearchPlan,
)
from backend.contracts.ports import QueryEmbeddingPort
from backend.contracts.schemas.core import PortError
from backend.modules.dynamic_rag.query_plan import prepare_query


class OpenSearchRetriever:
    def __init__(
        self, settings: Settings, encoder: QueryEmbeddingPort | None,
        *, password: SecretStr | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings, self.encoder = settings, encoder
        cfg = settings.rag
        url = httpx.URL(cfg.endpoint)
        if (
            url.scheme != "https" or url.host not in cfg.allowed_hosts
            or url.username or url.password or url.query or url.fragment
            or url.path not in ("", "/")
            or not re.fullmatch(r"[a-z0-9_-]+", cfg.read_alias)
        ):
            raise PortError(settings.error("rag_configuration"))
        if password is None:
            path = Path(cfg.credential_file)
            if not path.is_absolute():
                path = BACKEND_ROOT / path
            values = dotenv_values(path, interpolate=False) if path.is_file() else {}
            value = values.get(cfg.password_name)
            if not value:
                raise PortError(settings.error("rag_configuration"))
            password = SecretStr(value)
        verify = ssl.create_default_context(cafile=cfg.ca_file)
        if cfg.local_dev_certificate:
            if url.host not in {"localhost", "127.0.0.1", "::1"}:
                raise PortError(settings.error("rag_configuration"))
            verify.check_hostname = False
            verify.verify_mode = ssl.CERT_NONE
        self.client = httpx.AsyncClient(
            base_url=cfg.endpoint, auth=(cfg.username, password.get_secret_value()),
            verify=verify, timeout=cfg.search_timeout_seconds,
            follow_redirects=False, trust_env=False, transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.client.aclose()

    async def _search(self, body: dict) -> list[dict]:
        try:
            response = await self.client.post(
                f"/{self.settings.rag.read_alias}/_search", json=body,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("timed_out") or data.get("_shards", {}).get("failed", 0):
                raise ValueError("incomplete_search")
            return data["hits"]["hits"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise PortError(self.settings.error("rag_search_failed", retryable=True)) from None

    async def _scope(self, plan: SearchPlan, filters: RetrievalFilters):
        """Verify title against corpus metadata, then share constraints across both channels."""
        clauses = []
        notices = list(plan.notices)
        if filters.current_only or plan.version_scope == "current":
            clauses.append({"term": {"is_current": True}})
            if filters.current_only and plan.version_scope == "historical":
                notices.append("trusted_version_filter_preserved")
        elif plan.version_scope == "historical":
            clauses.append({"term": {"is_current": False}})
        if filters.document_id:
            clauses.append({"term": {"document_id": filters.document_id}})
        if filters.kind:
            clauses.append({"term": {"kind": filters.kind}})
        if plan.document_name:
            title = plan.document_name
            matches = await self._search({
                "size": self.settings.rag.candidates,
                "_source": ["document_name"],
                "collapse": {"field": "document_name.keyword"},
                "query": {"bool": {
                    "must": [{"match_phrase": {"document_name": title}}],
                    "filter": clauses,
                }},
            })
            names = sorted({hit.get("_source", {}).get("document_name", "") for hit in matches})
            exact = [name for name in names if name == title]
            suffix = [name for name in names if name.endswith(title)]
            accepted = exact or suffix
            if accepted:
                # Prefer the shortest canonical title, retaining all its versions.
                length = min(map(len, accepted))
                accepted = [name for name in accepted if len(name) == length]
                clauses.append({"terms": {"document_name.keyword": accepted}})
                notices.append("document_scope_verified")
            else:
                notices.append("document_scope_unresolved")
        if plan.article_nos:
            clauses.append({"terms": {"article_no.keyword": plan.article_nos}})
            if not filters.kind:
                clauses.append({"term": {"kind": "article"}})
            notices.append("article_scope_applied")
        return clauses, notices

    async def search(self, query: str | SearchPlan, filters: RetrievalFilters, limit: int) -> SearchBatch:
        cfg = self.settings.rag
        plan = prepare_query(RagQueryPlan(query=query), cfg) if isinstance(query, str) else query
        began_scope = perf_counter()
        clauses, notices = await self._scope(plan, filters)
        scope_seconds = perf_counter() - began_scope
        began = perf_counter()
        vector = None
        try:
            if self.encoder is None:
                raise PortError(self.settings.error("rag_embedding_failed"))
            semantic = " ".join(dict.fromkeys(filter(None, [
                plan.document_name, " ".join(plan.article_nos), plan.query, plan.content_query,
            ])))[:cfg.max_query_chars]
            vector = await self.encoder.encode(semantic)
            if len(vector) != cfg.embedding_dimensions:
                raise PortError(self.settings.error("rag_embedding_failed"))
            vector_query = vector_body(vector, clauses, cfg.candidates)
        except (PortError, ValueError, TypeError) as exc:
            if isinstance(exc, PortError) and exc.error.code == "rag_invalid_query":
                raise
            if not cfg.allow_bm25_fallback:
                raise PortError(self.settings.error("rag_embedding_failed")) from None
            vector = None
            notices.append("embedding_unavailable_bm25_fallback")
        encoded = perf_counter()
        keyword_query = bm25_body(plan.content_query or plan.query, clauses, cfg.candidates)
        query_bool = keyword_query["query"]["bool"]
        if plan.article_nos:
            # Exact locations must remain retrievable even with noisy optional keywords.
            query_bool["must"] = [{"match_all": {}}]
        query_bool["should"] = [
            clause for phrase in plan.anchors for clause in (
                {"match_phrase": {"chunk_text": {"query": phrase, "boost": cfg.anchor_phrase_boost}}},
                {"match": {"chunk_text": {"query": phrase, "operator": "and", "boost": cfg.anchor_terms_boost}}},
            )
        ]
        if plan.anchors:
            notices.append("content_anchor_applied")
        requests = [self._search(keyword_query)]
        if vector is not None:
            requests.append(self._search(vector_query))
        results = await asyncio.gather(*requests, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        bm = results[0]
        ve = results[1] if vector is not None else []
        try:
            rows = rrf(bm, ve, size=limit, k=cfg.rrf_k)
            items = []
            for row in rows:
                value = dict(row["chunk"])
                for name in ("document_name", "article_no", "section_path"):
                    value[name] = value.get(name) or ""
                value["version_date"] = value.get("version_date") or None
                value["retrieval_score"] = row["rrf_score"]
                item = Evidence.model_validate(value)
                if not all((item.chunk_id, item.document_id, item.parent_id, item.chunk_text)):
                    raise ValueError("missing_identity_or_text")
                items.append(item)
        except (ValidationError, ValueError, KeyError, TypeError):
            raise PortError(self.settings.error("rag_invalid_evidence")) from None
        return SearchBatch(
            items=items, method="hybrid" if vector is not None else "bm25",
            notices=notices, embedding_seconds=encoded - began,
            search_seconds=perf_counter() - encoded + scope_seconds,
        )
