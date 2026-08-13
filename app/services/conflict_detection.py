"""Exact-parcel and geometry-overlap conflict detection."""

from decimal import Decimal
import uuid

from shapely.geometry import shape
from sqlalchemy import select, text

from app.db.models import Claim, ClaimConflict, Parcel


INACTIVE_STATUSES = {"rejected", "superseded"}


def ordered_claim_pair(claim_a_id: uuid.UUID, claim_b_id: uuid.UUID):
    return tuple(sorted((claim_a_id, claim_b_id), key=str))


def _existing_conflict(session, a_id, b_id, conflict_type):
    a_id, b_id = ordered_claim_pair(a_id, b_id)
    return session.scalar(select(ClaimConflict).where(
        ClaimConflict.claim_a_id == a_id, ClaimConflict.claim_b_id == b_id,
        ClaimConflict.conflict_type == conflict_type,
    ))


def _create_conflict(session, new_claim, existing_claim, conflict_type, area, percent):
    a_id, b_id = ordered_claim_pair(new_claim.id, existing_claim.id)
    conflict = _existing_conflict(session, a_id, b_id, conflict_type)
    if conflict is None:
        conflict = ClaimConflict(
            claim_a_id=a_id, claim_b_id=b_id, conflict_type=conflict_type,
            overlap_area_sqm=Decimal(str(round(area, 4))) if area is not None else None,
            overlap_percent=Decimal(str(round(percent, 4))) if percent is not None else None,
        )
        session.add(conflict)
        session.flush()
    return conflict


def detect_conflicts(session, new_claim: Claim, *, min_sqm=1.0, min_percent=1.0):
    conflicts = []
    active_claims = list(session.scalars(select(Claim).where(
        Claim.id != new_claim.id, Claim.status.not_in(INACTIVE_STATUSES)
    )))
    new_parcel = session.get(Parcel, new_claim.parcel_id)
    new_document = new_claim.document
    for existing in active_claims:
        existing_parcel = session.get(Parcel, existing.parcel_id)
        if new_document.sha256 == existing.document.sha256:
            conflicts.append(_create_conflict(
                session, new_claim, existing, "duplicate_document", None, None,
            ))
        if existing.parcel_id == new_claim.parcel_id:
            conflicts.append(_create_conflict(
                session, new_claim, existing, "same_parcel",
                float(new_parcel.official_area_sqm) if new_parcel.official_area_sqm else None, 100.0,
            ))
            continue
        if session.bind.dialect.name == "postgresql":
            intersects, area, new_area, existing_area = session.execute(text("""
                SELECT
                    ST_Intersects(a.geometry, b.geometry),
                    ST_Area(ST_Intersection(a.geometry, b.geometry)::geography),
                    ST_Area(a.geometry::geography),
                    ST_Area(b.geometry::geography)
                FROM parcels a, parcels b
                WHERE a.id = :new_id AND b.id = :existing_id
            """), {"new_id": new_parcel.id, "existing_id": existing_parcel.id}).one()
            if not intersects:
                continue
            denominator = min(float(new_area), float(existing_area))
            area = float(area)
        else:
            new_geometry, existing_geometry = shape(new_parcel.geometry), shape(existing_parcel.geometry)
            if not new_geometry.intersects(existing_geometry):
                continue
            intersection = new_geometry.intersection(existing_geometry)
            # Development fallback: units follow the supplied synthetic coordinate space.
            area = intersection.area
            denominator = min(new_geometry.area, existing_geometry.area)
        percent = area / denominator * 100 if denominator else 0
        if area < min_sqm or percent < min_percent:
            continue
        conflicts.append(_create_conflict(
            session, new_claim, existing, "spatial_overlap", area, percent,
        ))
    return conflicts
