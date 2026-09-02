"""Advisory planning referral and printable report endpoints."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user, require_admin, require_reviewer
from app.db.fra_completion_models import DSSReferral
from app.db.fra_models import FRAClaim
from app.db.fra_operational_models import SchemeCatalogEntry
from app.db.session import get_db
from app.models.fra_completion_schemas import DSSReferralCreate, DSSReferralUpdate
from app.models.fra_dss_schemas import DerivedDSSEvaluationCreate, SchemeCatalogCreate
from app.services.dss_engine import InvalidRuleError, evaluate_rules
from app.services.dss_facts import derive_facts, fact_values
from app.services.dss_referrals import (
    ReferralConflictError,
    ReferralValidationError,
    create_referral,
    list_recommendations,
    update_referral,
)
from app.services.fra_reports import (
    ADVISORY_WARNING,
    ReportNotFoundError,
    render_archive_report,
    render_claim_report,
    render_historical_evidence_report,
    render_village_report,
)
from app.services.scheme_catalog import CatalogValidationError, create_catalog_entry


router = APIRouter(prefix="/api/fra", tags=["FRA planning and reports"])


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")


def _commit(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=message) from error


def _recommendation_dict(item) -> dict:
    output = dict(item.output_json or {})
    return {
        "id": str(item.id),
        "claim_id": str(item.claim_id),
        "scheme_code": item.rule_set.scheme_code,
        "scheme_name": item.rule_set.display_name,
        "rule_version": item.rule_version,
        "outcome": item.outcome,
        "reasons": list(output.get("reasons") or []),
        "missing_inputs": list(output.get("missing_inputs") or []),
        "recommendation": output.get("recommendation"),
        "source_reference": item.rule_set.source_reference,
        "advisory_only": True,
        "warning": ADVISORY_WARNING,
    }


def _referral_dict(referral: DSSReferral) -> dict:
    return {
        "id": str(referral.id),
        "recommendation_id": str(referral.recommendation_id),
        "department": referral.department,
        "priority": referral.priority,
        "status": referral.status,
        "assigned_to": referral.assigned_to,
        "notes": referral.notes,
        "history": list(referral.history_json or []),
        "advisory_only": True,
        "revision": referral.revision,
        "warning": ADVISORY_WARNING,
    }


def _catalog_dict(entry: SchemeCatalogEntry) -> dict:
    return {
        "id": str(entry.id), "scheme_code": entry.scheme_code,
        "display_name": entry.display_name, "version": entry.version,
        "department": entry.department, "description": entry.description,
        "effective_from": entry.effective_from, "effective_to": entry.effective_to,
        "approving_authority": entry.approving_authority,
        "source_reference": entry.source_reference,
        "definition": dict(entry.definition_json or {}),
        "authoritative": entry.authoritative, "active": entry.active,
    }


@router.post("/dss/derive-and-evaluate", status_code=201)
def derive_and_evaluate(
    payload: DerivedDSSEvaluationCreate,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    claim = db.get(FRAClaim, payload.claim_id)
    if claim is None or (user.role not in {"reviewer", "admin"} and claim.submitted_by != user.id):
        raise HTTPException(status_code=404, detail="FRA claim not found.")
    try:
        snapshot = derive_facts(
            db, claim, payload.derivation_version, user.id, f"{idempotency_key}:facts",
            request_id=_request_id(request),
        )
        recommendations = evaluate_rules(
            db, claim_id=claim.id, facts=fact_values(snapshot), actor_id=user.id,
            idempotency_key=f"{claim.id}:{idempotency_key}:evaluation", request_id=_request_id(request),
            rule_set_ids=payload.rule_set_ids, fact_snapshot_id=snapshot.id,
            fact_sources=snapshot.sources_json,
        )
    except (InvalidRuleError, ValueError) as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    _commit(db, "The derived DSS evaluation conflicts with an existing request.")
    return {
        "fact_snapshot": {
            "id": str(snapshot.id), "derivation_version": snapshot.derivation_version,
            "facts": dict(snapshot.facts_json or {}), "sources": dict(snapshot.sources_json or {}),
        },
        "recommendations": [_recommendation_dict(item) for item in recommendations],
        "warning": ADVISORY_WARNING,
    }


@router.post("/dss/scheme-catalog", status_code=201)
def add_scheme_catalog_entry(
    payload: SchemeCatalogCreate,
    request: Request,
    user: AuthenticatedUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        entry = create_catalog_entry(
            db, payload.model_dump(), actor_id=user.id, request_id=_request_id(request)
        )
    except CatalogValidationError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    _commit(db, "That scheme catalogue code and version already exist.")
    return _catalog_dict(entry)


@router.get("/dss/scheme-catalog")
def get_scheme_catalog(
    scheme_code: str | None = None,
    active: bool | None = None,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = select(SchemeCatalogEntry)
    if scheme_code:
        statement = statement.where(SchemeCatalogEntry.scheme_code == scheme_code.strip().upper())
    if active is not None:
        statement = statement.where(SchemeCatalogEntry.active.is_(active))
    entries = db.scalars(statement.order_by(SchemeCatalogEntry.scheme_code, SchemeCatalogEntry.version)).all()
    return {"items": [_catalog_dict(entry) for entry in entries]}


@router.get("/dss/recommendations")
def get_recommendations(
    claim_id: uuid.UUID | None = None,
    outcome: str | None = None,
    scheme_code: str | None = None,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = list_recommendations(
        db, claim_id=claim_id, outcome=outcome, scheme_code=scheme_code
    )
    return {"items": [_recommendation_dict(item) for item in items], "warning": ADVISORY_WARNING}


@router.post("/dss/recommendations/{recommendation_id}/referrals", status_code=201)
def create_recommendation_referral(
    recommendation_id: uuid.UUID,
    payload: DSSReferralCreate,
    request: Request,
    user: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    try:
        referral = create_referral(
            db,
            recommendation_id=recommendation_id,
            department=payload.department,
            priority=payload.priority,
            actor_id=user.id,
            idempotency_key=payload.idempotency_key,
            assigned_to=payload.assigned_to,
            notes=payload.notes,
            request_id=_request_id(request),
        )
    except ReferralConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ReferralValidationError as error:
        status = 404 if "does not exist" in str(error) else 422
        raise HTTPException(status_code=status, detail=str(error)) from error
    _commit(db, "This recommendation already has a referral.")
    return _referral_dict(referral)


@router.patch("/dss/referrals/{referral_id}")
def patch_referral(
    referral_id: uuid.UUID,
    payload: DSSReferralUpdate,
    request: Request,
    user: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    referral = db.get(DSSReferral, referral_id)
    if referral is None:
        raise HTTPException(status_code=404, detail="DSS referral not found.")
    try:
        update_referral(
            db,
            referral,
            status=payload.status,
            notes=payload.notes,
            assigned_to=payload.assigned_to,
            actor_id=user.id,
            expected_revision=payload.expected_revision,
            request_id=_request_id(request),
        )
    except ReferralConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ReferralValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _commit(db, "The referral changed while it was being updated.")
    return _referral_dict(referral)


def _html_response(content: str) -> HTMLResponse:
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@router.get("/reports/archive/{record_id}", response_class=HTMLResponse)
def archive_report(
    record_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    try:
        return _html_response(render_archive_report(db, record_id, actor_id=user.id))
    except ReportNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/reports/claims/{claim_id}", response_class=HTMLResponse)
def claim_report(
    claim_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return _html_response(render_claim_report(db, claim_id, actor_id=user.id))
    except ReportNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/reports/claims/{claim_id}/historical-evidence", response_class=HTMLResponse)
def historical_evidence_report(
    claim_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return _html_response(render_historical_evidence_report(db, claim_id, actor_id=user.id))
    except (ReportNotFoundError, PermissionError) as error:
        raise HTTPException(status_code=404, detail="Historical evidence report not found.") from error


@router.get("/reports/villages/{village_id}", response_class=HTMLResponse)
def village_report(
    village_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return _html_response(render_village_report(db, village_id, actor_id=user.id))
    except ReportNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
