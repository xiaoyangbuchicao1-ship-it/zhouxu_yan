"""Public, human-readable source contracts.

The stable source key is only a lookup handle for the authenticated client;
the UI renders the other fields and never presents the key.
"""

from typing import Literal

from pydantic import Field

from backend.contracts.schemas.core import Contract


class SourceReference(Contract):
    source_id: str
    kind: Literal["law", "case", "paper", "web"]
    title: str
    locator: str | None = None
    section: str | None = None
    version_date: str | None = None
    version_label: str | None = None
    authors: str | None = None
    collection: str | None = None
    url: str | None = None
    domain: str | None = None
    snippet: str | None = None
    verified_read: bool | None = None
    used_in_answer: bool | None = None


class PaperOriginal(Contract):
    document_id: str
    sha256: str
    size_bytes: int
    page: int | None = None
    page_end: int | None = None
    page_count: int
    content_url: str


class SourceDocument(SourceReference):
    text: str
    source_url: str | None = None
    notices: list[str] = Field(default_factory=list)
    original: PaperOriginal | None = None
