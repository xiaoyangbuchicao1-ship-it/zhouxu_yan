"""Normalize tool-local candidates; never infer legal facts or conversation intent."""

import re

from backend.config.settings import RagSettings
from backend.contracts.modules.dynamic_rag import RagQueryPlan, SearchPlan
from backend.modules.dynamic_rag.article_locator import (
    _parse_number,
    normalize_article_no,
)
from backend.modules.dynamic_rag.query_anchors import extract_query_anchors

_NUMBER = r"[0-9]{1,4}|[零〇一二三四五六七八九十百千万两]+"
_LOCATION = re.compile(
    rf"第?\s*({_NUMBER})\s*条?\s*(?:至|到|[-—~～])\s*第?\s*({_NUMBER})\s*条?"
)
_SINGLE = re.compile(rf"第?\s*({_NUMBER})\s*条?")


def article_locations(value: str, max_span: int) -> list[str]:
    match = _LOCATION.fullmatch(value.strip())
    if match:
        start, end = (_parse_number(v) for v in match.groups())
        if (
            start is None
            or end is None
            or not (0 < start <= end < 10000)
            or end - start + 1 > max_span
        ):
            return []
        return [normalize_article_no(str(n)) for n in range(start, end + 1)]
    match = _SINGLE.fullmatch(value.strip())
    normalized = normalize_article_no(match.group(1)) if match else None
    return [normalized] if normalized else []


def prepare_query(value: RagQueryPlan, cfg: RagSettings) -> SearchPlan:
    anchors = extract_query_anchors(
        value.query,
        noise_terms=tuple(cfg.query_noise_terms),
        limit=cfg.content_anchor_limit,
        max_chars=cfg.content_anchor_max_chars,
    )
    notes = []
    title = value.document_name or anchors.locator.document_title
    title = title.strip("《》 ") if title else None
    if title and len(title) > cfg.document_name_max_chars:
        title = None
        notes.append("invalid_document_name_ignored")
    location = value.location
    if not location:
        range_match = re.search(
            rf"第\s*({_NUMBER})\s*条?\s*(?:至|到|[-—~～])\s*第?\s*({_NUMBER})\s*条",
            value.query,
        )
        location = range_match.group(0) if range_match else None
    locations = article_locations(location, cfg.max_article_span) if location else []
    if not location:
        numbers = re.findall(rf"第\s*({_NUMBER})\s*条", value.query)
        locations = list(
            dict.fromkeys(filter(None, (normalize_article_no(n) for n in numbers)))
        )
        if len(locations) > cfg.max_article_span:
            locations = []
            notes.append("invalid_location_ignored")
    if location and not locations:
        notes.append("invalid_location_ignored")
    content = value.content_query or anchors.content_query
    content = content[: cfg.max_query_chars]
    phrases = list(
        dict.fromkeys(
            [
                *(a for a in value.anchors if len(a) <= cfg.content_anchor_max_chars),
                *anchors.phrases,
            ]
        )
    )[: cfg.content_anchor_limit]
    return SearchPlan(
        query=value.query.strip(),
        document_name=title,
        article_nos=locations,
        content_query=content,
        anchors=phrases,
        version_scope=value.version_scope,
        notices=notes,
    )


def followup_plan(query: str, original: SearchPlan, cfg: RagSettings) -> SearchPlan:
    """Expand content within the established scope; do not silently change filters."""
    plan = prepare_query(RagQueryPlan(query=query), cfg)
    return plan.model_copy(
        update={
            "document_name": original.document_name,
            "article_nos": list(original.article_nos),
            "version_scope": original.version_scope,
        }
    )


def changes_location(query: str, original: SearchPlan, cfg: RagSettings) -> bool:
    """A different explicit location requires a new tool call, not a futile retry."""
    candidate = prepare_query(RagQueryPlan(query=query), cfg)
    return bool(
        original.article_nos
        and candidate.article_nos
        and not set(candidate.article_nos).issubset(original.article_nos)
    )
