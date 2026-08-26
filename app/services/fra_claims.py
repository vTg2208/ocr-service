"""Creation, geometry versioning, and legacy promotion for FRA claims."""

from decimal import Decimal

from sqlalchemy import select

from app.db.fra_models import FRAClaim, FRAGeometryVersion, GramSabha, RightsHolder
from app.db.models import Claim, Document, Parcel, User
from app.services.audit import record_audit


RIGHT_TYPES = {"IFR", "CR", "CFR"}
IFR_HOLDER_TYPES = {"individual", "household"}
COMMUNITY_HOLDER_TYPES = {"community"}


class FRAClaimValidationError(ValueError):
    pass


def _require(session, model, identifier, label: str):
    value = session.get(model, identifier)
    if value is None:
        raise FRAClaimValidationError(f"{label} does not exist.")
    return value


def create_claim(
    session,
    *,
    claim_number: str,
    right_type: str,
    rights_holder_id,
    submitted_by,
    gram_sabha_id=None,
    parcel_id=None,
    document_id=None,
    claimed_area_sqm=None,
    provenance: dict | None = None,
    request_id: str | None = None,
) -> FRAClaim:
    normalized_number = claim_number.strip()
    normalized_type = right_type.strip().upper()
    if not normalized_number:
        raise FRAClaimValidationError("A claim number is required.")
    if normalized_type not in RIGHT_TYPES:
        raise FRAClaimValidationError("Right type must be IFR, CR, or CFR.")

    holder = _require(session, RightsHolder, rights_holder_id, "Rights holder")
    _require(session, User, submitted_by, "Submitting staff actor")
    gram_sabha = None
    if gram_sabha_id is not None:
        gram_sabha = _require(session, GramSabha, gram_sabha_id, "Gram Sabha")
    if parcel_id is not None:
        _require(session, Parcel, parcel_id, "Parcel")
    if document_id is not None:
        _require(session, Document, document_id, "Document")

    if normalized_type == "IFR" and holder.holder_type not in IFR_HOLDER_TYPES:
        raise FRAClaimValidationError("An IFR claim requires an individual or household rights holder.")
    if normalized_type in {"CR", "CFR"}:
        if gram_sabha is None:
            raise FRAClaimValidationError("A Gram Sabha is required for CR and CFR claims.")
        if holder.holder_type not in COMMUNITY_HOLDER_TYPES:
            raise FRAClaimValidationError("A CR or CFR claim requires a community rights holder.")
        if holder.gram_sabha_id is not None and holder.gram_sabha_id != gram_sabha.id:
            raise FRAClaimValidationError("Rights holder and claim must reference the same Gram Sabha.")

    claim = FRAClaim(
        claim_number=normalized_number,
        right_type=normalized_type,
        status="draft",
        rights_holder_id=holder.id,
        gram_sabha_id=gram_sabha.id if gram_sabha else None,
        submitted_by=submitted_by,
        parcel_id=parcel_id,
        document_id=document_id,
        claimed_area_sqm=(
            Decimal(str(claimed_area_sqm)) if claimed_area_sqm is not None else None
        ),
        provenance_json=dict(provenance or {}),
    )
    session.add(claim)
    session.flush()
    record_audit(
        session,
        actor_id=submitted_by,
        action="fra_claim_created",
        entity_type="fra_claim",
        entity_id=claim.id,
        after={"claim_number": normalized_number, "right_type": normalized_type, "status": "draft"},
        request_id=request_id,
    )
    return claim


def add_geometry_version(
    session,
    claim: FRAClaim,
    *,
    geometry: dict,
    source: str,
    provenance: dict,
    boundary_quality: str,
    actor_id,
    request_id: str | None = None,
) -> FRAGeometryVersion:
    if not isinstance(geometry, dict) or geometry.get("type") != "MultiPolygon":
        raise FRAClaimValidationError("FRA geometry must be a GeoJSON MultiPolygon.")
    source_name = source.strip()
    if not source_name:
        raise FRAClaimValidationError("Geometry source is required.")
    versions = list(claim.geometry_versions)
    version = FRAGeometryVersion(
        claim=claim,
        version=max((item.version for item in versions), default=0) + 1,
        geometry=geometry,
        source=source_name,
        provenance_json=dict(provenance),
        boundary_quality=boundary_quality.strip() or "unknown",
        created_by=actor_id,
    )
    session.add(version)
    session.flush()
    record_audit(
        session,
        actor_id=actor_id,
        action="fra_geometry_version_added",
        entity_type="fra_claim",
        entity_id=claim.id,
        after={"geometry_version_id": str(version.id), "version": version.version, "source": source_name},
        request_id=request_id,
    )
    return version


def promote_legacy_claim(
    session,
    *,
    legacy_claim_id,
    rights_holder_id,
    right_type: str,
    actor_id,
    gram_sabha_id=None,
    request_id: str | None = None,
) -> FRAClaim:
    existing = session.scalar(
        select(FRAClaim).where(FRAClaim.legacy_claim_id == legacy_claim_id)
    )
    if existing is not None:
        return existing

    legacy = _require(session, Claim, legacy_claim_id, "Legacy claim")
    claim = create_claim(
        session,
        claim_number=f"LEGACY-{legacy.id}",
        right_type=right_type,
        rights_holder_id=rights_holder_id,
        submitted_by=actor_id,
        gram_sabha_id=gram_sabha_id,
        parcel_id=legacy.parcel_id,
        document_id=legacy.document_id,
        claimed_area_sqm=legacy.claimed_area_sqm,
        provenance={
            "source": "legacy_claim_promotion",
            "legacy_claim_id": str(legacy.id),
            "legacy_status": legacy.status,
            "legacy_confirmed_fields": dict(legacy.confirmed_fields_json or {}),
        },
        request_id=request_id,
    )
    claim.legacy_claim_id = legacy.id
    if legacy.parcel is not None:
        add_geometry_version(
            session,
            claim,
            geometry=legacy.parcel.geometry,
            source="legacy_cadastral_parcel",
            provenance={
                "parcel_id": str(legacy.parcel.id),
                "source": legacy.parcel.source,
                "source_version": legacy.parcel.source_version,
                "source_record_id": legacy.parcel.source_record_id,
            },
            boundary_quality=legacy.parcel.boundary_quality,
            actor_id=actor_id,
            request_id=request_id,
        )
    session.flush()
    record_audit(
        session,
        actor_id=actor_id,
        action="legacy_claim_promoted",
        entity_type="fra_claim",
        entity_id=claim.id,
        after={"legacy_claim_id": str(legacy.id)},
        request_id=request_id,
    )
    return claim
