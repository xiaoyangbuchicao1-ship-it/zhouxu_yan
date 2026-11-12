"""Case identities and ranges are distinct from legislation article/version labels."""

from pydantic import Field

from backend.contracts.schemas.core import Contract


class CaseSearchInput(Contract):
    query: str = Field(min_length=1, max_length=1000)
    case_id: str | None = None
    limit: int | None = Field(default=None, ge=1, le=20)


class CaseReadInput(Contract):
    case_id: str = Field(min_length=1, max_length=100)
    snapshot_id: str | None = None
    start: int = Field(default=0, ge=0)
    chars: int | None = Field(default=None, ge=1, le=24000)
