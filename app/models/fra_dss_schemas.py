"""Strict contracts for derived DSS evaluations and scheme catalogue versions."""

from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DerivedDSSEvaluationCreate(StrictModel):
    claim_id: UUID
    derivation_version: str = Field(default="tn-facts-v1", min_length=1, max_length=100)
    rule_set_ids: list[UUID] | None = Field(default=None, max_length=100)


class SchemeCatalogCreate(StrictModel):
    scheme_code: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    effective_from: date | None = None
    effective_to: date | None = None
    approving_authority: str | None = Field(default=None, max_length=255)
    source_reference: str = Field(min_length=1, max_length=500)
    definition: dict[str, Any] = Field(default_factory=dict)
    authoritative: bool = False
    active: bool = False


__all__ = ["DerivedDSSEvaluationCreate", "SchemeCatalogCreate"]
