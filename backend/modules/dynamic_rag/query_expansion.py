"""Bounded, domain-neutral query expansion for missing-evidence rounds."""

from backend.modules.dynamic_rag.query_anchors import extract_query_anchors


def _normalized(value: str) -> str:
    return "".join(value.split()).casefold()


def expand_query(
    original_query: str,
    current_query: str,
    missing_aspects: list[str],
    previous_queries: list[str],
    *,
    noise_terms: tuple[str, ...] = (),
    anchor_limit: int = 6,
    anchor_max_chars: int = 160,
    max_chars: int = 1000,
) -> str | None:
    """Build one focused variant while preserving identity and unresolved text."""
    original = extract_query_anchors(
        original_query,
        noise_terms=noise_terms,
        limit=anchor_limit,
        max_chars=anchor_max_chars,
    )
    current = extract_query_anchors(
        current_query,
        noise_terms=noise_terms,
        limit=anchor_limit,
        max_chars=anchor_max_chars,
    )
    pieces: list[str] = []
    for value in (
        original.locator.document_title,
        original.locator.article_no,
        original.content_query,
        current.content_query,
        *missing_aspects,
    ):
        value = " ".join(str(value or "").split()).strip()
        if value and value not in pieces:
            pieces.append(value)
    candidate = " ".join(pieces)[:max_chars].strip()
    if not candidate or _normalized(candidate) in {
        _normalized(value) for value in previous_queries
    }:
        return None
    return candidate
