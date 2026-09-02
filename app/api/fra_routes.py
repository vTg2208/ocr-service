"""Authenticated orchestration routes for the FRA foundation domain."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import (
    AuthenticatedUser,
    get_current_user,
    require_admin,
    require_reviewer,
)
from app.db.fra_models import (
    DSSRecommendation,
    FRAClaim,
    FRAEvidenceItem,
    FRAGeometryVersion,
    GramSabha,
    RightsHolder,
    SchemeRuleSet,
)
from app.db.models import Document
from app.db.session import get_db
from app.models.fra_schemas import (
    DSSEvaluationCreate,
    EvidenceCreate,
    FRAClaimCreate,
    GeometryCreate,
    GramSabhaCreate,
    LegacyPromotionCreate,
    RightsHolderCreate,
    SatelliteObservationCreate,
    SchemeRuleSetCreate,
    SpatialEvaluationCreate,
    TitleCreate,
    TransitionCreate,
)
from app.services.audit import record_audit
from app.services.dss_engine import InvalidRuleError, evaluate_rules, validate_rule_definition
from app.services.fra_claims import (
    FRAClaimValidationError,
    add_geometry_version,
    create_claim,
    promote_legacy_claim,
)
from app.services.fra_spatial_policy import evaluate_spatial_compatibility
from app.services.fra_reference_spatial import evaluate_reference_intersections
from app.services.fra_workflow import (
    InvalidTransitionError,
    TitleIssuanceError,
    issue_title,
    transition_claim,
)
from app.services.satellite_evidence import (
    ImageryRequest,
    ImageryScene,
    LocalManifestImageryProvider,
    LocalObservationAnalyser,
    SatelliteEvidenceValidationError,
    SatelliteProviderUnavailable,
    acquire_and_analyse,
)


router = APIRouter(prefix="/api/fra", tags=["FRA foundation"])


def _request_id(request: Request) -> str:
    return (
        getattr(request.state, "request_id", None)
        or request.headers.get("X-Request-ID")
        or str(uuid.uuid4())
    )


def _commit(db: Session, message: str = "The record conflicts with existing data.") -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=message) from exc


def _claim_or_404(db: Session, claim_id: uuid.UUID) -> FRAClaim:
    claim = db.get(FRAClaim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="FRA claim not found.")
    return claim


def _holder_dict(holder: RightsHolder, user: AuthenticatedUser) -> dict:
    result = {
        "id": str(holder.id),
        "display_name": holder.display_name,
        "holder_type": holder.holder_type,
        "claimant_category": holder.claimant_category,
        "gram_sabha_id": str(holder.gram_sabha_id) if holder.gram_sabha_id else None,
        "metadata": dict(holder.metadata_json or {}),
    }
    if user.role in {"reviewer", "admin"}:
        result["external_reference"] = holder.external_reference
    return result


def _claim_dict(claim: FRAClaim, user: AuthenticatedUser, *, detailed: bool = False) -> dict:
    result = {
        "id": str(claim.id),
        "claim_number": claim.claim_number,
        "right_type": claim.right_type,
        "status": claim.status,
        "rights_holder_id": str(claim.rights_holder_id),
        "gram_sabha_id": str(claim.gram_sabha_id) if claim.gram_sabha_id else None,
        "submitted_by": str(claim.submitted_by),
        "legacy_claim_id": str(claim.legacy_claim_id) if claim.legacy_claim_id else None,
        "parcel_id": str(claim.parcel_id) if claim.parcel_id else None,
        "document_id": str(claim.document_id) if claim.document_id else None,
        "claimed_area_sqm": float(claim.claimed_area_sqm) if claim.claimed_area_sqm else None,
        "provenance": dict(claim.provenance_json or {}),
    }
    if detailed:
        result.update({
            "rights_holder": _holder_dict(claim.rights_holder, user),
            "geometry_versions": [
                {
                    "id": str(item.id), "version": item.version, "geometry": item.geometry,
                    "source": item.source, "boundary_quality": item.boundary_quality,
                    "provenance": dict(item.provenance_json or {}),
                }
                for item in claim.geometry_versions
            ],
            "decisions": [
                {
                    "id": str(item.id), "from_status": item.from_status,
                    "to_status": item.to_status, "authority_level": item.authority_level,
                    "outcome": item.outcome, "reasons": list(item.reasons_json or []),
                }
                for item in claim.decisions
            ],
            "titles": [
                {
                    "id": str(item.id), "title_number": item.title_number,
                    "version": item.version, "active": item.active,
                    "geometry_version_id": str(item.geometry_version_id) if item.geometry_version_id else None,
                }
                for item in claim.titles
            ],
        })
    return result


@router.post("/rights-holders", status_code=201)
def create_rights_holder(
    payload: RightsHolderCreate,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.gram_sabha_id and db.get(GramSabha, payload.gram_sabha_id) is None:
        raise HTTPException(status_code=404, detail="Gram Sabha not found.")
    holder = RightsHolder(
        display_name=payload.display_name.strip(),
        holder_type=payload.holder_type,
        claimant_category=payload.claimant_category,
        external_reference=payload.external_reference,
        gram_sabha_id=payload.gram_sabha_id,
        metadata_json=payload.metadata,
    )
    db.add(holder); db.flush()
    record_audit(
        db, actor_id=user.id, action="rights_holder_created", entity_type="rights_holder",
        entity_id=holder.id, after={"holder_type": holder.holder_type},
        request_id=_request_id(request),
    )
    _commit(db, "A rights holder with that external reference already exists.")
    return _holder_dict(holder, user)


@router.post("/gram-sabhas", status_code=201)
def create_gram_sabha(
    payload: GramSabhaCreate,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    gram_sabha = GramSabha(
        name=payload.name.strip(), village=payload.village.strip(),
        gram_panchayat=payload.gram_panchayat, block=payload.block,
        district=payload.district, state=payload.state,
        external_reference=payload.external_reference, boundary=payload.boundary,
        metadata_json=payload.metadata,
    )
    db.add(gram_sabha); db.flush()
    record_audit(
        db, actor_id=user.id, action="gram_sabha_created", entity_type="gram_sabha",
        entity_id=gram_sabha.id, after={"name": gram_sabha.name, "village": gram_sabha.village},
        request_id=_request_id(request),
    )
    _commit(db, "A Gram Sabha with that external reference already exists.")
    return {
        "id": str(gram_sabha.id), "name": gram_sabha.name, "village": gram_sabha.village,
        "district": gram_sabha.district, "state": gram_sabha.state,
    }


@router.post("/claims", status_code=201)
def create_fra_claim(
    payload: FRAClaimCreate,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        claim = create_claim(
            db, claim_number=payload.claim_number, right_type=payload.right_type,
            rights_holder_id=payload.rights_holder_id, submitted_by=user.id,
            gram_sabha_id=payload.gram_sabha_id, parcel_id=payload.parcel_id,
            document_id=payload.document_id, claimed_area_sqm=payload.claimed_area_sqm,
            provenance=payload.provenance, request_id=_request_id(request),
        )
    except FRAClaimValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _commit(db, "An FRA claim with that claim number already exists.")
    return _claim_dict(claim, user)


@router.post("/claims/promote-legacy/{legacy_claim_id}", status_code=201)
def promote_claim(
    legacy_claim_id: uuid.UUID,
    payload: LegacyPromotionCreate,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        claim = promote_legacy_claim(
            db, legacy_claim_id=legacy_claim_id, rights_holder_id=payload.rights_holder_id,
            right_type=payload.right_type, actor_id=user.id,
            gram_sabha_id=payload.gram_sabha_id, request_id=_request_id(request),
        )
    except FRAClaimValidationError as exc:
        status = 404 if "does not exist" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    _commit(db)
    return _claim_dict(claim, user, detailed=True)


@router.get("/claims/{claim_id}")
def get_fra_claim(
    claim_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _claim_dict(_claim_or_404(db, claim_id), user, detailed=True)


@router.post("/claims/{claim_id}/geometries", status_code=201)
def create_geometry(
    claim_id: uuid.UUID,
    payload: GeometryCreate,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    claim = _claim_or_404(db, claim_id)
    try:
        version = add_geometry_version(
            db, claim, geometry=payload.geometry, source=payload.source,
            provenance=payload.provenance, boundary_quality=payload.boundary_quality,
            actor_id=user.id, request_id=_request_id(request),
        )
    except FRAClaimValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _commit(db)
    return {
        "id": str(version.id), "claim_id": str(claim.id), "version": version.version,
        "geometry": version.geometry, "source": version.source,
        "boundary_quality": version.boundary_quality, "provenance": version.provenance_json,
    }


@router.post("/claims/{claim_id}/evidence", status_code=201)
def create_evidence(
    claim_id: uuid.UUID,
    payload: EvidenceCreate,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    claim = _claim_or_404(db, claim_id)
    if payload.category == "satellite_observation":
        raise HTTPException(
            status_code=422,
            detail="Satellite evidence must be created through the satellite-observations endpoint.",
        )
    if payload.document_id and db.get(Document, payload.document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    evidence = FRAEvidenceItem(
        claim=claim, category=payload.category, legal_role="submitted", source=payload.source,
        description=payload.description, document_id=payload.document_id,
        provenance_json=payload.provenance, captured_at=payload.captured_at,
        verification_state="unverified", source_verified=False, created_by=user.id,
    )
    db.add(evidence); db.flush()
    record_audit(
        db, actor_id=user.id, action="fra_evidence_created", entity_type="fra_claim",
        entity_id=claim.id, after={"evidence_id": str(evidence.id), "category": evidence.category},
        request_id=_request_id(request),
    )
    _commit(db)
    return {
        "id": str(evidence.id), "claim_id": str(claim.id), "category": evidence.category,
        "legal_role": evidence.legal_role, "source": evidence.source,
        "description": evidence.description, "verification_state": evidence.verification_state,
        "source_verified": evidence.source_verified,
    }


@router.post("/claims/{claim_id}/transitions")
def transition_fra_claim(
    claim_id: uuid.UUID,
    payload: TransitionCreate,
    request: Request,
    user: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    claim = _claim_or_404(db, claim_id)
    try:
        decision = transition_claim(
            db, claim, target_status=payload.target_status,
            authority_level=payload.authority_level, outcome=payload.outcome,
            reasons=payload.reasons, actor_id=user.id, request_id=_request_id(request),
        )
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "allowed_states": sorted(exc.allowed_states)},
        ) from exc
    _commit(db)
    return {
        "id": str(decision.id), "claim_id": str(claim.id),
        "from_status": decision.from_status, "to_status": decision.to_status,
        "outcome": decision.outcome, "reasons": decision.reasons_json,
    }


@router.post("/claims/{claim_id}/titles", status_code=201)
def create_title(
    claim_id: uuid.UUID,
    payload: TitleCreate,
    request: Request,
    user: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    claim = _claim_or_404(db, claim_id)
    if payload.geometry_version_id:
        geometry = db.get(FRAGeometryVersion, payload.geometry_version_id)
        if geometry is None or geometry.claim_id != claim.id:
            raise HTTPException(status_code=422, detail="Geometry version does not belong to this claim.")
    try:
        title = issue_title(
            db, claim, title_number=payload.title_number,
            geometry_version_id=payload.geometry_version_id, issued_by=user.id,
            metadata=payload.metadata, request_id=_request_id(request),
        )
    except TitleIssuanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _commit(db, "A title with that title number already exists.")
    return {
        "id": str(title.id), "claim_id": str(claim.id), "title_number": title.title_number,
        "version": title.version, "active": title.active,
        "geometry_version_id": str(title.geometry_version_id) if title.geometry_version_id else None,
    }


@router.post("/claims/{claim_id}/spatial-evaluation")
def spatial_evaluation(
    claim_id: uuid.UUID,
    payload: SpatialEvaluationCreate,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    claim = _claim_or_404(db, claim_id)
    result = evaluate_spatial_compatibility(
        db, claim, payload.geometry, min_sqm=payload.min_sqm,
        min_percent=payload.min_percent, policy_version=payload.policy_version,
    )
    reference_findings = evaluate_reference_intersections(
        db, payload.geometry, set(payload.reference_kinds), payload.reference_policy_version
    )
    privileged = user.role in {"reviewer", "admin"}
    claim_findings = [
        {
            **({"related_claim_id": str(item.related_claim_id)} if privileged else {}),
            "existing_right_type": item.existing_right_type,
            "outcome": item.outcome,
            "reason": item.reason,
            "overlap_area_sqm": item.overlap_area_sqm,
            "overlap_percent": item.overlap_percent,
        }
        for item in result.findings
    ]
    serialized_reference_findings = [
        {
            **({
                "reference_feature_id": str(item.reference_feature_id),
                "reference_source_authority": item.reference_source_authority,
                "reference_source_version": item.reference_source_version,
                "source_record_id": item.source_record_id,
            } if privileged else {}),
            "dataset_kind": item.dataset_kind,
            "outcome": item.outcome,
            "reason": item.reason,
            "overlap_area_sqm": item.overlap_area_sqm,
            "overlap_percent": item.overlap_percent,
            "policy_version": item.policy_version,
        }
        for item in reference_findings
    ]
    combined_outcome = (
        "blocked" if result.outcome == "blocked"
        else "review_required"
        if result.outcome == "review_required" or any(
            item.outcome == "review_required" for item in reference_findings
        )
        else "allowed"
    )
    body = {
        "outcome": combined_outcome,
        "policy_version": result.policy_version,
        "reference_policy_version": payload.reference_policy_version,
        "findings": claim_findings,
        "claim_findings": claim_findings,
        "reference_findings": serialized_reference_findings,
    }
    return JSONResponse(status_code=409 if combined_outcome == "blocked" else 200, content=body)


@router.post("/claims/{claim_id}/satellite-observations", status_code=201)
def create_satellite_observations(
    claim_id: uuid.UUID,
    payload: SatelliteObservationCreate,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    claim = _claim_or_404(db, claim_id)
    if not claim.geometry_versions:
        raise HTTPException(status_code=422, detail="The claim requires a geometry version.")
    geometry = max(claim.geometry_versions, key=lambda item: item.version).geometry
    scenes = {}
    if payload.source_uri and payload.acquired_at and payload.observations:
        scenes[payload.scene_id] = ImageryScene(
            scene_id=payload.scene_id, provider=payload.provider,
            source_uri=payload.source_uri, acquired_at=payload.acquired_at,
            metadata={"observations": [item.model_dump() for item in payload.observations]},
        )
    try:
        observations = acquire_and_analyse(
            db, claim, request=ImageryRequest(scene_id=payload.scene_id, geometry=geometry),
            provider=LocalManifestImageryProvider(scenes),
            analyser=LocalObservationAnalyser(payload.analyser_version), actor_id=user.id,
            request_id=_request_id(request),
        )
    except SatelliteProviderUnavailable as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SatelliteEvidenceValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _commit(db)
    return [
        {
            "id": str(item.id), "claim_id": str(claim.id), "scene_id": item.scene_id,
            "provider": item.provider, "asset_class": item.asset_class,
            "observed_value": item.observed_value_json.get("value"),
            "confidence": float(item.confidence), "analyser_version": item.analyser_version,
            "acquired_at": item.acquired_at.isoformat(), "legal_role": "supporting",
            "verification_state": "unverified", "source_verified": False,
        }
        for item in observations
    ]


@router.post("/dss/rule-sets", status_code=201)
def create_rule_set(
    payload: SchemeRuleSetCreate,
    request: Request,
    user: AuthenticatedUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        validate_rule_definition(payload.condition)
    except InvalidRuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.effective_from and payload.effective_to and payload.effective_to < payload.effective_from:
        raise HTTPException(status_code=422, detail="effective_to cannot precede effective_from.")
    rule = SchemeRuleSet(
        scheme_code=payload.scheme_code.strip(), display_name=payload.display_name.strip(),
        version=payload.version.strip(), effective_from=payload.effective_from,
        effective_to=payload.effective_to, required_facts_json=payload.required_facts,
        condition_json=payload.condition, recommendation_text=payload.recommendation_text,
        source_reference=payload.source_reference, active=payload.active, created_by=user.id,
    )
    db.add(rule); db.flush()
    record_audit(
        db, actor_id=user.id, action="dss_rule_set_created", entity_type="scheme_rule_set",
        entity_id=rule.id, after={"scheme_code": rule.scheme_code, "version": rule.version},
        request_id=_request_id(request),
    )
    _commit(db, "That DSS scheme code and version already exist.")
    return {
        "id": str(rule.id), "scheme_code": rule.scheme_code,
        "display_name": rule.display_name, "version": rule.version,
        "source_reference": rule.source_reference, "active": rule.active,
        "advisory_only": True,
    }


def _recommendation_dict(recommendation: DSSRecommendation) -> dict:
    return {"id": str(recommendation.id), "claim_id": str(recommendation.claim_id), **dict(recommendation.output_json)}


@router.post("/dss/evaluate", status_code=201)
def evaluate_dss(
    payload: DSSEvaluationCreate,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user: AuthenticatedUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(status_code=422, detail="Idempotency-Key header is required.")
    if db.get(FRAClaim, payload.claim_id) is None:
        raise HTTPException(status_code=404, detail="FRA claim not found.")
    try:
        recommendations = evaluate_rules(
            db, claim_id=payload.claim_id, facts=payload.facts, actor_id=user.id,
            idempotency_key=idempotency_key, request_id=_request_id(request),
        )
    except (InvalidRuleError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _commit(db)
    return [_recommendation_dict(item) for item in recommendations]


@router.get("/dss/recommendations/{recommendation_id}")
def get_dss_recommendation(
    recommendation_id: uuid.UUID,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    recommendation = db.get(DSSRecommendation, recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="DSS recommendation not found.")
    return _recommendation_dict(recommendation)
