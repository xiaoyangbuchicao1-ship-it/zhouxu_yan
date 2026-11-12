"""Generic identity and content anchors for retrieval query planning."""

import re
from dataclasses import dataclass

from backend.modules.dynamic_rag.article_locator import (
    ArticleLocator,
    locate_article,
)


@dataclass(frozen=True)
class QueryAnchors:
    """Searchable anchors without domain-specific field names."""

    locator: ArticleLocator
    content_query: str = ""
    phrases: tuple[str, ...] = ()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _remove_identity(text: str, locator: ArticleLocator) -> str:
    value = re.sub(r"《[^》]{2,100}》", " ", text)
    if locator.document_title:
        value = value.replace(locator.document_title, " ")
    value = re.sub(
        r"第\s*(?:[0-9]{1,4}|[零〇一二三四五六七八九十百千万两]+)\s*条",
        " ",
        value,
    )
    return value


def _phrases(text: str, noise_terms: tuple[str, ...], limit: int) -> tuple[str, ...]:
    value = text
    for term in sorted((item for item in noise_terms if item), key=len, reverse=True):
        value = value.replace(term, " ")
    parts = re.split(r"[，。；;：:？?！!、\n]+|以及|并且|或者|和|及|与", value)
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = _clean_text(part).strip(" ,，。；;:：")
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= limit:
            break
    return tuple(result)


def extract_query_anchors(
    query: str,
    *,
    noise_terms: tuple[str, ...] = (),
    limit: int = 6,
    max_chars: int = 160,
) -> QueryAnchors:
    """Extract document/location identity and configurable content phrases."""
    locator = locate_article(query)
    content = _remove_identity(query, locator)
    phrases = _phrases(content, noise_terms, limit)
    content_query = _clean_text(" ".join(phrases))[:max_chars]
    return QueryAnchors(locator=locator, content_query=content_query, phrases=phrases)
