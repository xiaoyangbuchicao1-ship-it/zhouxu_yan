"""Contracts for research information cards and method cards.

Information cards are evidence candidates. Capability cards are method
resources. Keeping the contracts separate prevents a paper author's claim from
being treated as a current legal rule or a method instruction from becoming
evidence.
"""

from __future__ import annotations

from pydantic import Field

from backend.contracts.schemas.core import Contract


class InformationCardSearchInput(Contract):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=6, ge=1, le=20)
    evidence_type: str | None = Field(default=None, min_length=1, max_length=80)
    topics: list[str] = Field(default_factory=list, max_length=8)


class InformationCardReadInput(Contract):
    card_id: str = Field(min_length=1, max_length=160)


class CapabilityCardSelectInput(Contract):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=3, ge=1, le=8)


class CapabilityCardReadInput(Contract):
    candidate_id: str = Field(min_length=1, max_length=160)
