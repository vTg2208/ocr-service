"""API response contracts for staged FRA reference datasets."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpatialFeaturePreview(StrictModel):
    id: UUID
    source_record_id: str
    geometry: dict[str, Any]
    properties: dict[str, Any]
    repaired: bool = False


class SpatialImportSummary(StrictModel):
    id: UUID
    dataset_kind: str
    source_authority: str
    source_version: str
    state: str
    declared_crs: str | None
    detected_crs: str | None
    record_count: int = Field(ge=0)
    valid_count: int = Field(ge=0)
    invalid_count: int = Field(ge=0)
    repaired_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    synthetic: bool
    classification: Literal[
        "synthetic", "declared_authoritative",
        "published_synthetic_reference", "published_authoritative_reference",
    ]


class SpatialImportPreview(SpatialImportSummary):
    errors: list[dict[str, Any]]
    features: list[SpatialFeaturePreview]
