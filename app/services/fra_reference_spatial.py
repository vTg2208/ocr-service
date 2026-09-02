"""Non-adjudicative intersections with published Tamil Nadu reference layers."""

from dataclasses import dataclass
import json
import uuid

from shapely.geometry import shape
from sqlalchemy import func, select

from app.db.fra_operational_models import SpatialReferenceFeature
from app.services.fra_spatial_policy import _overlap_metrics


REFERENCE_KINDS = {
    "administrative_boundary",
    "protected_area",
    "forest_compartment",
    "water_body",
    "cadastral_parcel",
}


@dataclass(frozen=True)
class ReferenceSpatialFinding:
    reference_feature_id: uuid.UUID
    dataset_kind: str
    outcome: str
    reason: str
    overlap_area_sqm: float
    overlap_percent: float
    reference_source_authority: str
    reference_source_version: str
    source_record_id: str
    policy_version: str


def _candidate_features(session, geometry: dict, kinds: set[str]):
    statement = (
        select(SpatialReferenceFeature)
        .where(
            SpatialReferenceFeature.published.is_(True),
            SpatialReferenceFeature.dataset_kind.in_(sorted(kinds)),
        )
        .order_by(
            SpatialReferenceFeature.dataset_kind,
            SpatialReferenceFeature.source_authority,
            SpatialReferenceFeature.source_version,
            SpatialReferenceFeature.source_record_id,
        )
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        candidate = func.ST_SetSRID(func.ST_GeomFromGeoJSON(json.dumps(geometry)), 4326)
        statement = statement.where(
            func.ST_Intersects(SpatialReferenceFeature.geometry, candidate)
        )
    return list(session.scalars(statement))


def evaluate_reference_intersections(
    session,
    geometry: dict,
    kinds: set[str],
    policy_version: str,
) -> list[ReferenceSpatialFinding]:
    if not isinstance(geometry, dict) or geometry.get("type") != "MultiPolygon":
        raise ValueError("Reference evaluation requires a GeoJSON MultiPolygon.")
    selected = {str(kind).strip().casefold() for kind in kinds}
    unsupported = selected - REFERENCE_KINDS
    if unsupported:
        raise ValueError(f"Unsupported reference kinds: {', '.join(sorted(unsupported))}.")
    version = policy_version.strip()
    if not version:
        raise ValueError("A reference spatial policy version is required.")
    candidate_shape = shape(geometry)
    findings = []
    for feature in _candidate_features(session, geometry, selected):
        reference_shape = shape(feature.geometry)
        if not candidate_shape.intersects(reference_shape):
            continue
        overlap_area, overlap_percent = _overlap_metrics(
            session, geometry, feature.geometry
        )
        if overlap_area <= 0:
            continue
        if feature.dataset_kind == "administrative_boundary":
            contained = reference_shape.covers(candidate_shape)
            outcome = "consistent" if contained else "review_required"
            reason = (
                "within_administrative_boundary"
                if contained else "crosses_administrative_boundary"
            )
        elif feature.dataset_kind == "forest_compartment":
            outcome, reason = "context", "intersects_forest_compartment"
        else:
            outcome = "review_required"
            reason = {
                "protected_area": "intersects_protected_area",
                "water_body": "intersects_water_body",
                "cadastral_parcel": "overlaps_cadastral_reference",
            }[feature.dataset_kind]
        findings.append(ReferenceSpatialFinding(
            reference_feature_id=feature.id,
            dataset_kind=feature.dataset_kind,
            outcome=outcome,
            reason=reason,
            overlap_area_sqm=round(overlap_area, 4),
            overlap_percent=round(overlap_percent, 4),
            reference_source_authority=feature.source_authority,
            reference_source_version=feature.source_version,
            source_record_id=feature.source_record_id,
            policy_version=version,
        ))
    return findings


__all__ = ["ReferenceSpatialFinding", "evaluate_reference_intersections"]
