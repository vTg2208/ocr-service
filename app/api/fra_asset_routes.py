"""Protected FRA asset inference and reviewer endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user, require_reviewer
from app.db.fra_completion_models import AssetFeature, ModelVersion
from app.db.session import get_db
from app.models.fra_completion_schemas import AssetInferenceJobCreate, AssetReviewCreate
from app.services.fra_assets import (
    AssetReviewConflict,
    AssetValidationError,
    enqueue_asset_inference,
    list_assets,
    review_asset,
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
    return {
        "id": str(asset.id),
        "village_id": str(asset.village_id) if asset.village_id else None,
        "claim_id": str(asset.claim_id) if asset.claim_id else None,
        "asset_class": asset.asset_class,
        "geometry": asset.polygon_geometry or asset.point_geometry_json,
        "observed_value": dict(asset.observed_value_json or {}),
        "acquired_at": asset.acquired_at.isoformat() if asset.acquired_at else None,
        "confidence": float(asset.confidence) if asset.confidence is not None else None,
        "source_type": asset.source_type,
        "verification_state": asset.verification_state,
        "verification_reasons": list(asset.verification_reasons_json or []),
        "supersedes_id": str(asset.supersedes_id) if asset.supersedes_id else None,
        "synthetic": asset.synthetic,
        "revision": asset.revision,
        "provenance": {
            key: value
            for key, value in (asset.provenance_json or {}).items()
            if key not in {"source_uri", "artifact_uri", "private_uri"}
        },
    }


@router.post("/inference-jobs", status_code=202)
def create_asset_inference_job(
    payload: AssetInferenceJobCreate,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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
    _user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    assets = list_assets(
        db,
        district=district,
        block=block,
        village=village,
        claim_id=claim_id,
        asset_class=asset_class,
        verification_state=verification_state,
    )
    return {"items": [_asset_dict(asset) for asset in assets], "warning": SUPPORTING_WARNING}


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
    except AssetReviewConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except AssetValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _commit(db, "The asset changed while it was being reviewed.")
    return {**_asset_dict(result), "warning": SUPPORTING_WARNING}
