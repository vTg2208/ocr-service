"""Privacy-aware native FRA case queries for the staff workspace."""

from sqlalchemy import select

from app.db.fra_models import FRAClaim
from app.db.models import AuditEvent
from app.services.fra_workflow import allowed_transitions


PRIVATE_KEYS = {"private_uri", "artifact_uri", "source_uri", "storage_key"}


def _safe_mapping(value):
    if isinstance(value, dict):
        return {
            key: _safe_mapping(item)
            for key, item in value.items()
            if key not in PRIVATE_KEYS and "private" not in key.casefold()
        }
    if isinstance(value, list):
        return [_safe_mapping(item) for item in value]
    if isinstance(value, str) and value.casefold().startswith("private://"):
        return "[private source redacted]"
    return value


def _gram_sabha(claim: FRAClaim):
    return claim.gram_sabha or claim.rights_holder.gram_sabha


def _location(claim: FRAClaim) -> dict:
    gram_sabha = _gram_sabha(claim)
    if gram_sabha is not None:
        return {
            "district": gram_sabha.district,
            "block": gram_sabha.block,
            "village": gram_sabha.village,
        }
    if claim.parcel is not None:
        return {
            "district": claim.parcel.district,
            "block": claim.parcel.taluk,
            "village": claim.parcel.village,
        }
    return {"district": None, "block": None, "village": None}


def case_summary(claim: FRAClaim) -> dict:
    return {
        "id": str(claim.id),
        "claim_number": claim.claim_number,
        "right_type": claim.right_type,
        "status": claim.status,
        "rights_holder": claim.rights_holder.display_name,
        "claimed_area_sqm": float(claim.claimed_area_sqm) if claim.claimed_area_sqm else None,
        "location": _location(claim),
        "geometry_version_count": len(claim.geometry_versions),
        "evidence_count": len(claim.evidence_items),
        "active_title_count": sum(1 for item in claim.titles if item.active),
        "created_at": claim.created_at.isoformat(),
    }


def list_cases(
    session,
    *,
    user_id,
    privileged: bool,
    status: str | None = None,
    right_type: str | None = None,
    district: str | None = None,
    block: str | None = None,
    village: str | None = None,
    query: str | None = None,
) -> list[FRAClaim]:
    statement = select(FRAClaim).order_by(FRAClaim.created_at.desc(), FRAClaim.id)
    if not privileged:
        statement = statement.where(FRAClaim.submitted_by == user_id)
    if status:
        statement = statement.where(FRAClaim.status == status)
    if right_type:
        statement = statement.where(FRAClaim.right_type == right_type.upper())
    claims = list(session.scalars(statement).unique())
    term = (query or "").strip().casefold()
    result = []
    for claim in claims:
        location = _location(claim)
        if district and (location["district"] or "").casefold() != district.casefold():
            continue
        if block and (location["block"] or "").casefold() != block.casefold():
            continue
        if village and (location["village"] or "").casefold() != village.casefold():
            continue
        if term and term not in " ".join(
            [claim.claim_number, claim.rights_holder.display_name, *(value or "" for value in location.values())]
        ).casefold():
            continue
        result.append(claim)
    return result


def can_view_case(claim: FRAClaim, *, user_id, privileged: bool) -> bool:
    return privileged or claim.submitted_by == user_id


def case_detail(session, claim: FRAClaim, *, privileged: bool) -> dict:
    audit_events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.entity_type == "fra_claim", AuditEvent.entity_id == claim.id)
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ).all()
    detail = {
        **case_summary(claim),
        "legacy_claim_id": str(claim.legacy_claim_id) if claim.legacy_claim_id else None,
        "parcel_id": str(claim.parcel_id) if claim.parcel_id else None,
        "document_id": str(claim.document_id) if claim.document_id else None,
        "provenance": _safe_mapping(dict(claim.provenance_json or {})),
        "rights_holder": {
            "id": str(claim.rights_holder.id),
            "display_name": claim.rights_holder.display_name,
            "holder_type": claim.rights_holder.holder_type,
            "claimant_category": claim.rights_holder.claimant_category,
        },
        "gram_sabha": (
            {
                "id": str(_gram_sabha(claim).id),
                "name": _gram_sabha(claim).name,
                "village": _gram_sabha(claim).village,
                "block": _gram_sabha(claim).block,
                "district": _gram_sabha(claim).district,
            }
            if _gram_sabha(claim)
            else None
        ),
        "allowed_transitions": sorted(allowed_transitions(claim.status)),
        "geometry_versions": [
            {
                "id": str(item.id), "version": item.version, "geometry": item.geometry,
                "source": item.source, "boundary_quality": item.boundary_quality,
                "provenance": _safe_mapping(dict(item.provenance_json or {})),
                "created_at": item.created_at.isoformat(),
            }
            for item in claim.geometry_versions
        ],
        "evidence_items": [
            {
                "id": str(item.id), "category": item.category, "legal_role": item.legal_role,
                "source": item.source, "description": item.description,
                "verification_state": item.verification_state,
                "source_verified": item.source_verified,
                "captured_at": item.captured_at.isoformat() if item.captured_at else None,
                "created_at": item.created_at.isoformat(),
            }
            for item in claim.evidence_items
        ],
        "decisions": [
            {
                "id": str(item.id), "from_status": item.from_status,
                "to_status": item.to_status, "authority_level": item.authority_level,
                "outcome": item.outcome, "reasons": list(item.reasons_json or []),
                "created_at": item.created_at.isoformat(),
            }
            for item in claim.decisions
        ],
        "titles": [
            {
                "id": str(item.id), "title_number": item.title_number,
                "version": item.version, "active": item.active,
                "geometry_version_id": str(item.geometry_version_id) if item.geometry_version_id else None,
                "issued_at": item.issued_at.isoformat(),
            }
            for item in claim.titles
        ],
        "recommendations": [
            {
                "id": str(item.id), "outcome": item.outcome,
                "rule_version": item.rule_version,
                "output": _safe_mapping(dict(item.output_json or {})),
                "created_at": item.created_at.isoformat(),
            }
            for item in claim.dss_recommendations
        ],
        "audit_timeline": [
            {
                "id": str(item.id), "action": item.action,
                "before": _safe_mapping(item.before_json) if privileged else None,
                "after": _safe_mapping(item.after_json) if privileged else None,
                "created_at": item.created_at.isoformat(),
            }
            for item in audit_events
        ],
    }
    return detail
