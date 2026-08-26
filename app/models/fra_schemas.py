"""Validated request contracts for the protected FRA foundation API."""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from shapely.geometry import shape


RightType = Literal["IFR", "CR", "CFR"]
HolderType = Literal["individual", "household", "community"]
EvidenceCategory = Literal[
    "oral_statement", "documentary", "physical", "map", "satellite_observation"
]
AssetClass = Literal["agricultural_cover", "forest_cover", "water_body", "homestead"]
LifecycleTarget = Literal[
    "submitted", "gram_sabha_verified", "sdlc_review", "dlc_decided", "granted",
    "rejected", "remanded", "withdrawn", "superseded",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RightsHolderCreate(StrictModel):
    display_name: str = Field(min_length=1, max_length=255)
    holder_type: HolderType
    claimant_category: str | None = Field(default=None, max_length=32)
    external_reference: str | None = Field(default=None, max_length=255)
    gram_sabha_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GramSabhaCreate(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    village: str = Field(min_length=1, max_length=255)
    gram_panchayat: str | None = Field(default=None, max_length=255)
    block: str | None = Field(default=None, max_length=255)
    district: str | None = Field(default=None, max_length=255)
    state: str | None = Field(default=None, max_length=255)
    external_reference: str | None = Field(default=None, max_length=255)
    boundary: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("boundary")
    @classmethod
    def validate_boundary(cls, value):
        return normalize_geometry(value) if value is not None else None


class FRAClaimCreate(StrictModel):
    claim_number: str = Field(min_length=1, max_length=100)
    right_type: RightType
    rights_holder_id: UUID
    gram_sabha_id: UUID | None = None
    parcel_id: UUID | None = None
    document_id: UUID | None = None
    claimed_area_sqm: float | None = Field(default=None, gt=0)
    provenance: dict[str, Any] = Field(default_factory=dict)


class LegacyPromotionCreate(StrictModel):
    rights_holder_id: UUID
    right_type: RightType
    gram_sabha_id: UUID | None = None


def normalize_geometry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("type") not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Geometry must be a GeoJSON Polygon or MultiPolygon.")
    normalized = (
        {"type": "MultiPolygon", "coordinates": [value.get("coordinates")]}
        if value.get("type") == "Polygon"
        else value
    )
    try:
        parsed = shape(normalized)
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("Geometry is not valid GeoJSON.") from exc
    if parsed.is_empty or not parsed.is_valid or parsed.geom_type != "MultiPolygon":
        raise ValueError("Geometry must be a valid, non-empty Polygon or MultiPolygon.")
    return normalized


class GeometryCreate(StrictModel):
    geometry: dict[str, Any]
    source: str = Field(min_length=1, max_length=100)
    provenance: dict[str, Any] = Field(default_factory=dict)
    boundary_quality: str = Field(default="unknown", min_length=1, max_length=50)

    @field_validator("geometry")
    @classmethod
    def validate_geometry(cls, value):
        return normalize_geometry(value)


class EvidenceCreate(StrictModel):
    category: EvidenceCategory
    source: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1)
    document_id: UUID | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    captured_at: date | None = None


class TransitionCreate(StrictModel):
    target_status: LifecycleTarget
    authority_level: str = Field(min_length=1, max_length=32)
    outcome: str = Field(min_length=1, max_length=64)
    reasons: list[str] = Field(default_factory=list)


class TitleCreate(StrictModel):
    title_number: str = Field(min_length=1, max_length=100)
    geometry_version_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SpatialEvaluationCreate(StrictModel):
    geometry: dict[str, Any]
    min_sqm: float = Field(default=1.0, ge=0)
    min_percent: float = Field(default=0.1, ge=0, le=100)
    policy_version: str = Field(default="fra-spatial-v1", min_length=1, max_length=100)

    @field_validator("geometry")
    @classmethod
    def validate_geometry(cls, value):
        return normalize_geometry(value)


class SatelliteAssetInput(StrictModel):
    asset_class: AssetClass
    value: str | int | float | bool
    confidence: float = Field(ge=0, le=1)


class SatelliteObservationCreate(StrictModel):
    scene_id: str = Field(min_length=1, max_length=255)
    provider: Literal["local-manifest"] = "local-manifest"
    source_uri: str | None = Field(default=None, max_length=500)
    acquired_at: date | None = None
    observations: list[SatelliteAssetInput] | None = None
    analyser_version: str = Field(default="local-v1", min_length=1, max_length=100)


class SchemeRuleSetCreate(StrictModel):
    scheme_code: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=100)
    effective_from: date | None = None
    effective_to: date | None = None
    required_facts: list[str] = Field(default_factory=list)
    condition: dict[str, Any]
    recommendation_text: str = Field(min_length=1)
    source_reference: str = Field(min_length=1, max_length=500)
    active: bool = True


class DSSEvaluationCreate(StrictModel):
    claim_id: UUID
    facts: dict[str, Any] = Field(default_factory=dict)
