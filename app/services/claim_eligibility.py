"""Atomic availability checks for exclusive land claims."""

from dataclasses import dataclass
import uuid

from shapely.geometry import shape
from sqlalchemy import select, text

from app.db.models import Claim, Parcel


INACTIVE_STATUSES = {"rejected", "superseded"}
CLAIM_REGISTRY_LOCK = 731_945_117


@dataclass
class ClaimUnavailableError(ValueError):
    reason: str
    blocking_claim_id: uuid.UUID

    def __str__(self) -> str:
        return "This land is already claimed."


def _lock_registry(session) -> None:
    """Serialize availability checks in PostgreSQL to prevent overlap races."""
    if session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": CLAIM_REGISTRY_LOCK},
        )


def ensure_land_available(session, parcel_id, *, min_sqm=1.0, min_percent=1.0) -> None:
    """Raise when an active claim already owns this parcel or overlapping land."""
    _lock_registry(session)

    exact = session.scalar(select(Claim).where(
        Claim.parcel_id == parcel_id,
        Claim.status.not_in(INACTIVE_STATUSES),
    ))
    if exact is not None:
        raise ClaimUnavailableError("same_parcel", exact.id)

    candidate = session.get(Parcel, parcel_id)
    if candidate is None:
        raise ValueError("Selected parcel does not exist.")

    if session.bind.dialect.name == "postgresql":
        blocker = session.execute(text("""
            SELECT c.id,
                   ST_Area(ST_Intersection(candidate.geometry, claimed.geometry)::geography) AS overlap_area,
                   LEAST(
                       ST_Area(candidate.geometry::geography),
                       ST_Area(claimed.geometry::geography)
                   ) AS smaller_area
            FROM claims c
            JOIN parcels claimed ON claimed.id = c.parcel_id
            JOIN parcels candidate ON candidate.id = :parcel_id
            WHERE c.status NOT IN ('rejected', 'superseded')
              AND ST_Intersects(candidate.geometry, claimed.geometry)
            ORDER BY c.submitted_at
        """), {"parcel_id": parcel_id}).all()
        for claim_id, overlap_area, smaller_area in blocker:
            area = float(overlap_area or 0)
            percent = area / float(smaller_area) * 100 if smaller_area else 0
            if area >= min_sqm and percent >= min_percent:
                raise ClaimUnavailableError("spatial_overlap", claim_id)
        return None

    candidate_geometry = shape(candidate.geometry)
    active = session.execute(
        select(Claim, Parcel)
        .join(Parcel, Parcel.id == Claim.parcel_id)
        .where(Claim.status.not_in(INACTIVE_STATUSES))
        .order_by(Claim.submitted_at)
    ).all()
    for claim, claimed_parcel in active:
        claimed_geometry = shape(claimed_parcel.geometry)
        intersection = candidate_geometry.intersection(claimed_geometry)
        area = intersection.area
        denominator = min(candidate_geometry.area, claimed_geometry.area)
        percent = area / denominator * 100 if denominator else 0
        if area >= min_sqm and percent >= min_percent:
            raise ClaimUnavailableError("spatial_overlap", claim.id)
    return None
