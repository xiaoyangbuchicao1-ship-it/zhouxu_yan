"""Public, human-readable source contracts.

The stable source key is only a lookup handle for the authenticated client;
the UI renders the other fields and never presents the key.
"""

from typing import Literal

from pydantic import Field

from backend.contracts.schemas.core import Contract


class SourceReference(Contract):
    source_id: str
    kind: Literal["law", "case"]
    title: str
    locator: str | None = None
    section: str | None = None
    version_date: str | None = None
    version_label: str | None = None


class SourceDocument(SourceReference):
    text: str
    source_url: str | None = None
    notices: list[str] = Field(default_factory=list)
