"""Right-type-aware spatial compatibility for FRA claims."""

from dataclasses import dataclass, field
import json
import math
import uuid

from shapely.geometry import shape
from shapely.ops import transform
from sqlalchemy import select, text

from app.db.fra_models import FRAClaim


EARTH_RADIUS_METRES = 6_371_008.8
INACTIVE_STATUSES = {"draft", "rejected", "withdrawn", "superseded"}


@dataclass(frozen=True)
class SpatialFinding:
    related_claim_id: uuid.UUID
    existing_right_type: str
    outcome: str
    reason: str
    overlap_area_sqm: float | None = None
    overlap_percent: float | None = None


@dataclass(frozen=True)
class SpatialEvaluation:
    outcome: str
    policy_version: str
    findings: list[SpatialFinding] = field(default_factory=list)


def _project(geometry, latitude_origin: float):
    cosine = math.cos(math.radians(latitude_origin))

    def to_metres(x, y, z=None):
        projected = (
            math.radians(x) * EARTH_RADIUS_METRES * cosine,
            math.radians(y) * EARTH_RADIUS_METRES,
        )
        return (*projected, z) if z is not None else projected

    return transform(to_metres, geometry)


def _area_sqm(geometry: dict) -> float:
    parsed = shape(geometry)
    if parsed.is_empty:
        return 0.0
    return float(_project(parsed, parsed.centroid.y).area)


def _sqlite_overlap_metrics(candidate: dict, existing: dict) -> tuple[float, float]:
    candidate_shape = shape(candidate)
    existing_shape = shape(existing)
    if candidate_shape.is_empty or existing_shape.is_empty:
        return 0.0, 0.0
    latitude_origin = (candidate_shape.centroid.y + existing_shape.centroid.y) / 2
    candidate_projected = _project(candidate_shape, latitude_origin)
    existing_projected = _project(existing_shape, latitude_origin)
    intersection_area = float(candidate_projected.intersection(existing_projected).area)
    smaller_area = min(float(candidate_projected.area), float(existing_projected.area))
    percent = intersection_area / smaller_area * 100 if smaller_area else 0.0
    return intersection_area, percent


def _postgis_overlap_metrics(session, candidate: dict, existing: dict) -> tuple[float, float]:
    row = session.execute(
        text(
            """
            WITH geometries AS (
              SELECT
                ST_SetSRID(ST_GeomFromGeoJSON(:candidate), 4326) AS candidate,
                ST_SetSRID(ST_GeomFromGeoJSON(:existing), 4326) AS existing
            )
            SELECT
              ST_Area(ST_Intersection(candidate, existing)::geography) AS overlap_area,
              LEAST(ST_Area(candidate::geography), ST_Area(existing::geography)) AS smaller_area
            FROM geometries
            """
        ),
        {"candidate": json.dumps(candidate), "existing": json.dumps(existing)},
    ).one()
    overlap_area = float(row.overlap_area or 0)
    smaller_area = float(row.smaller_area or 0)
    percent = overlap_area / smaller_area * 100 if smaller_area else 0.0
    return overlap_area, percent


def _overlap_metrics(session, candidate: dict, existing: dict) -> tuple[float, float]:
    if session.bind.dialect.name == "postgresql":
        return _postgis_overlap_metrics(session, candidate, existing)
    return _sqlite_overlap_metrics(candidate, existing)


def _current_geometry(claim: FRAClaim) -> dict | None:
    if not claim.geometry_versions:
        return None
    return max(claim.geometry_versions, key=lambda item: item.version).geometry


def evaluate_spatial_compatibility(
    session,
    claim: FRAClaim,
    geometry: dict,
    *,
    min_sqm: float,
    min_percent: float,
    policy_version: str = "fra-spatial-v1",
) -> SpatialEvaluation:
    if not isinstance(geometry, dict) or geometry.get("type") != "MultiPolygon":
        raise ValueError("Spatial evaluation requires a GeoJSON MultiPolygon.")

    existing_claims = list(
        session.scalars(
            select(FRAClaim)
            .where(
                FRAClaim.id != claim.id,
                FRAClaim.status.not_in(INACTIVE_STATUSES),
            )
            .order_by(FRAClaim.created_at, FRAClaim.id)
        )
    )
    findings: list[SpatialFinding] = []
    for existing in existing_claims:
        if (
            claim.right_type == "IFR"
            and existing.right_type == "IFR"
            and claim.parcel_id is not None
            and claim.parcel_id == existing.parcel_id
        ):
            findings.append(
                SpatialFinding(
                    related_claim_id=existing.id,
                    existing_right_type=existing.right_type,
                    outcome="blocked",
                    reason="same_parcel_exclusive_ifr",
                )
            )
            continue

        existing_geometry = _current_geometry(existing)
        if existing_geometry is None:
            continue
        overlap_area, overlap_percent = _overlap_metrics(
            session, geometry, existing_geometry
        )
        if overlap_area < min_sqm or overlap_percent < min_percent:
            continue
        if claim.right_type == "IFR" and existing.right_type == "IFR":
            outcome = "blocked"
            reason = "material_overlap_exclusive_ifr"
        else:
            outcome = "review_required"
            reason = "layered_or_shared_rights_review"
        findings.append(
            SpatialFinding(
                related_claim_id=existing.id,
                existing_right_type=existing.right_type,
                outcome=outcome,
                reason=reason,
                overlap_area_sqm=round(overlap_area, 4),
                overlap_percent=round(overlap_percent, 4),
            )
        )

    outcome = (
        "blocked"
        if any(item.outcome == "blocked" for item in findings)
        else "review_required"
        if findings
        else "allowed"
    )
    return SpatialEvaluation(
        outcome=outcome,
        policy_version=policy_version,
        findings=findings,
    )
