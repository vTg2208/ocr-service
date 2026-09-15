"""Protected FRA asset inference and reviewer endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.fra_access import claim_for_user, visible_claim_ids
from app.api.auth import AuthenticatedUser, get_current_user, require_reviewer
from app.db.fra_completion_models import AssetFeature, ModelVersion, VillageAssetProfile
from app.db.session import get_db
from app.models.fra_completion_schemas import AssetInferenceJobCreate, AssetReviewCreate
from app.services.fra_assets import (
    AssetReviewConflict,
    AssetValidationError,
    enqueue_asset_inference,
    list_assets,
    review_asset,
)
from app.services.asset_contracts import (
    ASSET_ALIASES,
    ASSET_CLASSES,
    ASSET_CLASS_LABELS,
    ASSET_TAXONOMY_VERSION,
    asset_subtype,
)
from app.services.village_asset_profiles import (
    claim_asset_context,
    list_village_asset_profiles,
    refresh_village_asset_profiles,
)


router = APIRouter(prefix="/api/fra/assets", tags=["FRA assets"])
SUPPORTING_WARNING = (
    "Model and satellite observations are supporting evidence and do not determine legal validity."
)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")


def _commit(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=message) from error


def _asset_dict(asset: AssetFeature) -> dict:
    geometry = asset.polygon_geometry or asset.point_geometry_json
    provenance = {
        key: value
        for key, value in (asset.provenance_json or {}).items()
        if key not in {"source_uri", "artifact_uri", "private_uri"}
    }
    source_reference = str(asset.source_reference or "")
    if source_reference.casefold().startswith(("private://", "private/")):
        source_reference = "[private source redacted]"
    model = asset.inference_run.model_version if asset.inference_run is not None else None
    model_name = model.name if model is not None else provenance.get("model_name")
    model_version = model.version if model is not None else provenance.get("model_version")
    return {
        "id": str(asset.id),
        "village_id": str(asset.village_id) if asset.village_id else None,
        "claim_id": str(asset.claim_id) if asset.claim_id else None,
        "asset_class": asset.asset_class,
        "asset_subtype": asset_subtype(asset.asset_class, asset.observed_value_json),
        "geometry": geometry,
        "observed_value": dict(asset.observed_value_json or {}),
        "acquired_at": asset.acquired_at.isoformat() if asset.acquired_at else None,
        "confidence": float(asset.confidence) if asset.confidence is not None else None,
        "source_type": asset.source_type,
        "verification_state": asset.verification_state,
        "verification_reasons": list(asset.verification_reasons_json or []),
        "supersedes_id": str(asset.supersedes_id) if asset.supersedes_id else None,
        "synthetic": asset.synthetic,
        "revision": asset.revision,
        "provenance": provenance,
        "evidence_chain": {
            "asset": {
                "class": asset.asset_class,
                "subtype": asset_subtype(asset.asset_class, asset.observed_value_json),
                "observed_value": dict(asset.observed_value_json or {}),
            },
            "imagery": {
                "reference": source_reference or None,
                "source_type": asset.source_type,
            },
            "date": asset.acquired_at.isoformat() if asset.acquired_at else None,
            "model": {
                "name": model_name,
                "version": model_version,
                "inference_run_id": str(asset.inference_run_id) if asset.inference_run_id else None,
            } if model_name or model_version or asset.inference_run_id else None,
            "confidence": float(asset.confidence) if asset.confidence is not None else None,
            "geometry": geometry,
            "verification": {
                "state": asset.verification_state,
                "reviewed_at": asset.verified_at.isoformat() if asset.verified_at else None,
                "reviewer_id": str(asset.verified_by) if asset.verified_by else None,
                "reasons": list(asset.verification_reasons_json or []),
            },
        },
    }


@router.post("/inference-jobs", status_code=202)
def create_asset_inference_job(
    payload: AssetInferenceJobCreate,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.claim_id:
        claim_for_user(db, payload.claim_id, user)
    model = db.get(ModelVersion, payload.model_version_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Model version not found.")
    if model.status != "active":
        raise HTTPException(status_code=503, detail="The selected asset model is unavailable.")
    try:
        job = enqueue_asset_inference(
            db,
            village_id=payload.village_id,
            claim_id=payload.claim_id,
            model_version_id=payload.model_version_id,
            scene_id=payload.scene_id,
            actor_id=user.id,
            idempotency_key=payload.idempotency_key,
            manifest=payload.manifest,
        )
    except AssetValidationError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    _commit(db, "That asset inference request already exists with conflicting data.")
    return {
        "id": str(job.id),
        "task_type": job.task_type,
        "state": job.state,
        "attempts": job.attempts,
        "warning": SUPPORTING_WARNING,
    }


@router.get("")
def get_assets(
    district: str | None = None,
    block: str | None = None,
    village: str | None = None,
    claim_id: uuid.UUID | None = None,
    asset_class: str | None = None,
    verification_state: str | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if claim_id:
        claim_for_user(db, claim_id, user)
    try:
        assets = list_assets(
            db,
            district=district,
            block=block,
            village=village,
            claim_id=claim_id,
            visible_claim_ids=visible_claim_ids(user),
            asset_class=asset_class,
            verification_state=verification_state,
        )
    except AssetValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"items": [_asset_dict(asset) for asset in assets], "warning": SUPPORTING_WARNING}


@router.get("/taxonomy")
def get_asset_taxonomy(
    _user: AuthenticatedUser = Depends(get_current_user),
):
    return {
        "version": ASSET_TAXONOMY_VERSION,
        "classes": [
            {"name": name, "label": ASSET_CLASS_LABELS[name]}
            for name in sorted(ASSET_CLASSES)
        ],
        "input_aliases": dict(sorted(ASSET_ALIASES.items())),
    }


@router.get("/village-profiles")
def get_village_asset_profiles(
    district: str | None = None,
    block: str | None = None,
    village: str | None = None,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profiles = list_village_asset_profiles(
        db, district=district, block=block, village=village
    )
    return {"items": [_profile_dict(item) for item in profiles], "warning": SUPPORTING_WARNING}


@router.get("/claims/{claim_id}/spatial-context")
def get_claim_asset_spatial_context(
    claim_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    claim = claim_for_user(db, claim_id, user)
    try:
        return claim_asset_context(db, claim)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/village-profiles/rebuild")
def rebuild_village_asset_profiles(
    request: Request,
    user: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    profiles = refresh_village_asset_profiles(
        db, actor_id=user.id, request_id=_request_id(request)
    )
    _commit(db, "Village asset profiles changed while they were being rebuilt.")
    return {"items": [_profile_dict(item) for item in profiles], "warning": SUPPORTING_WARNING}


def _profile_dict(profile: VillageAssetProfile) -> dict:
    sources = []
    for source in profile.sources_json or []:
        item = dict(source)
        reference = str(item.get("source_reference") or "")
        if reference.casefold().startswith(("private://", "private/")):
            item["source_reference"] = "[private source redacted]"
        sources.append(item)
    return {
        "id": str(profile.id),
        "village_id": str(profile.village_id),
        "district": profile.village.district_name,
        "block": profile.village.block_name,
        "village": profile.village.village_name,
        "taxonomy_version": profile.taxonomy_version,
        "calculation_version": profile.calculation_version,
        "source_asset_count": profile.source_asset_count,
        "verified_asset_count": profile.verified_asset_count,
        "pending_asset_count": profile.pending_asset_count,
        "metrics": dict(profile.metrics_json or {}),
        "sources": sources,
        "generated_at": profile.generated_at.isoformat(),
    }


@router.post("/{asset_id}/review")
def review_asset_feature(
    asset_id: uuid.UUID,
    payload: AssetReviewCreate,
    request: Request,
    user: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    asset = db.get(AssetFeature, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset feature not found.")
    if asset.claim_id:
        claim_for_user(db, asset.claim_id, user)
    try:
        result = review_asset(
            db,
            asset,
            outcome=payload.outcome,
            reviewer_id=user.id,
            reasons=payload.reasons,
            expected_revision=payload.expected_revision,
            corrected_value=payload.corrected_value,
            corrected_geometry=payload.corrected_geometry,
            request_id=_request_id(request),
        )
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="The operation conflicts with the current stored state.") from error
    except AssetReviewConflict as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except AssetValidationError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    _commit(db, "The asset changed while it was being reviewed.")
    return {**_asset_dict(result), "warning": SUPPORTING_WARNING}
