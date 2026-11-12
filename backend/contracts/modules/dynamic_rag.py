"""Retrieval contracts: evidence is data, never a model message or instruction."""

from typing import Literal

from pydantic import Field, field_validator

from backend.contracts.schemas.core import Contract, Error


class RetrievalFilters(Contract):
    current_only: bool = False
    document_id: str | None = None
    kind: Literal["article", "source"] | None = None


class RagQueryPlan(Contract):
    """Model-facing arguments for this tool only, without database identities."""

    query: str = Field(min_length=1)
    document_name: str | None = None
    location: str | None = None
    content_query: str | None = None
    anchors: list[str] = Field(default_factory=list)
    version_scope: Literal["current", "historical", "all"] = "all"
    limit: int | None = Field(default=None, ge=1)

    @field_validator("document_name", "location", "content_query", mode="before")
    @classmethod
    def optional_text(cls, value):
        return value.strip() or None if isinstance(value, str) else None

    @field_validator("anchors", mode="before")
    @classmethod
    def optional_anchors(cls, value):
        if not isinstance(value, list):
            return []
        return list(dict.fromkeys(s.strip() for s in value if isinstance(s, str) and s.strip()))

    @field_validator("version_scope", mode="before")
    @classmethod
    def optional_version(cls, value):
        return value if value in ("current", "historical", "all") else "all"

    @field_validator("limit", mode="before")
    @classmethod
    def optional_limit(cls, value):
        return value if type(value) is int and value > 0 else None


class RagInput(RagQueryPlan):
    """Internal input; filters can be supplied only by a trusted caller."""

    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)


class SearchPlan(Contract):
    query: str
    document_name: str | None = None
    article_nos: list[str] = Field(default_factory=list)
    content_query: str = ""
    anchors: list[str] = Field(default_factory=list)
    version_scope: Literal["current", "historical", "all"] = "all"
    notices: list[str] = Field(default_factory=list)


class Evidence(Contract):
    chunk_id: str
    document_id: str
    parent_id: str
    kind: Literal["article", "source"]
    chunk_text: str
    document_name: str = ""
    article_no: str = ""
    section_path: list[str] | str = Field(default_factory=list)
    is_current: bool | None = None
    version_date: str | None = None
    version_relation: str | None = None
    version_target_document_id: str | None = None
    version_target_parent_id: str | None = None
    chunk_index: int = 0
    chunk_count: int = 1
    retrieval_score: float | None = None


class SearchBatch(Contract):
    items: list[Evidence]
    method: Literal["hybrid", "bm25"]
    notices: list[str] = Field(default_factory=list)
    embedding_seconds: float = 0
    search_seconds: float = 0


class EvidenceReview(Contract):
    decision: Literal["sufficient", "search", "insufficient"]
    selected_ids: list[str] = Field(default_factory=list)
    missing_aspects: list[str] = Field(default_factory=list)
    next_query: str | None = None
    reason: str = ""
    total_tokens: int | None = None


class RetrievalStep(Contract):
    round: int
    query: str
    method: str
    found_ids: list[str]
    new_count: int
    embedding_seconds: float
    search_seconds: float
    review_seconds: float = 0
    decision: str | None = None
    reason: str = ""
    missing_aspects: list[str] = Field(default_factory=list)
    review_tokens: int | None = None
    plan: SearchPlan | None = None


class RagOutput(Contract):
    items: list[Evidence]
    stop_reason: str
    sufficiency: Literal["sufficient", "insufficient", "unassessed"] = "unassessed"
    missing_aspects: list[str] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    steps: list[RetrievalStep] = Field(default_factory=list)
    elapsed_seconds: float
    omitted_ids: list[str] = Field(default_factory=list)


class RagObservation(Contract):
    """Bounded model-facing observation; retrieval trace stays outside the prompt."""

    query: str
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    status: Literal["ok", "needs_input", "failed"]
    items: list[Evidence] = Field(default_factory=list)
    stop_reason: str
    sufficiency: Literal["sufficient", "insufficient", "unassessed"] = "unassessed"
    missing_aspects: list[str] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    omitted_ids: list[str] = Field(default_factory=list)
    error: Error | None = None
