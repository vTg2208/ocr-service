"""Claim-scoped historical evidence requests and redacted status views."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user, require_reviewer
from app.db.fra_completion_models import ProcessingJob
from app.db.fra_models import FRAClaim
from app.db.fra_operational_models import ImageryArtifact
from app.db.session import get_db
from app.models.fra_imagery_schemas import HistoricalEvidenceRequest, HistoricalEvidenceReview
from app.services.audit import record_audit
from app.services.historical_evidence import request_historical_evidence


router = APIRouter(prefix="/api/fra/claims", tags=["FRA historical evidence"])


def _claim_for_user(db: Session, claim_id: uuid.UUID, user: AuthenticatedUser) -> FRAClaim:
    claim = db.get(FRAClaim, claim_id)
    if claim is None or (user.role not in {"reviewer", "admin"} and claim.submitted_by != user.id):
        raise HTTPException(status_code=404, detail="FRA claim not found.")
    return claim


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")


@router.post("/{claim_id}/historical-evidence", status_code=202)
def create_historical_evidence(
    claim_id: uuid.UUID,
    payload: HistoricalEvidenceRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=255),
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    claim = _claim_for_user(db, claim_id, user)
    existing = db.scalar(select(ProcessingJob).where(
        ProcessingJob.task_type == "historical_evidence",
        ProcessingJob.entity_id == claim.id,
        ProcessingJob.idempotency_key == idempotency_key,
    ))
    try:
        job = request_historical_evidence(
            db, claim, target_years=payload.target_years, actor_id=user.id,
            idempotency_key=idempotency_key, request_id=_request_id(request),
        )
        db.commit()
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="The historical evidence request conflicts with an existing request.") from error
    return {
        "job_id": str(job.id),
        "state": job.state,
        "target_years": list((job.payload_json or {}).get("target_years", [])),
        "replayed": existing is not None,
    }


@router.get("/{claim_id}/historical-evidence")
def list_historical_evidence(
    claim_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    claim = _claim_for_user(db, claim_id, user)
    jobs = db.scalars(select(ProcessingJob).where(
        ProcessingJob.task_type == "historical_evidence",
        ProcessingJob.entity_id == claim.id,
    ).order_by(ProcessingJob.created_at.desc())).all()
    artifacts = db.scalars(select(ImageryArtifact).where(
        ImageryArtifact.claim_id == claim.id,
    ).order_by(ImageryArtifact.target_year, ImageryArtifact.created_at)).all()
    return {
        "jobs": [{
            "id": str(job.id), "state": job.state, "attempts": job.attempts,
            "error_code": job.error_code, "error_message": job.error_message,
            "target_years": list((job.payload_json or {}).get("target_years", [])),
            "result": dict(job.result_json or {}),
        } for job in jobs],
        "artifacts": [{
            "id": str(artifact.id), "target_year": artifact.target_year,
            "artifact_type": artifact.artifact_type, "state": artifact.state,
            "verification_state": artifact.verification_state,
            "processor_version": artifact.processor_version,
            "model_version": artifact.model_version.version if artifact.model_version else None,
            "statistics": dict(artifact.statistics_json or {}),
            "quality_flags": list(artifact.quality_flags_json or []),
            "provenance": dict(artifact.provenance_json or {}),
            "provider": artifact.imagery_scene.provider if artifact.imagery_scene else None,
            "collection": artifact.imagery_scene.collection if artifact.imagery_scene else None,
            "scene_id": artifact.imagery_scene.scene_id if artifact.imagery_scene else None,
            "acquired_at": artifact.imagery_scene.acquired_at if artifact.imagery_scene else None,
            "cloud_cover": float(artifact.imagery_scene.cloud_cover) if artifact.imagery_scene and artifact.imagery_scene.cloud_cover is not None else None,
            "license_reference": artifact.imagery_scene.license_reference if artifact.imagery_scene else None,
        } for artifact in artifacts],
    }


@router.patch("/{claim_id}/historical-evidence/{artifact_id}/review")
def review_historical_evidence(
    claim_id: uuid.UUID,
    artifact_id: uuid.UUID,
    payload: HistoricalEvidenceReview,
    request: Request,
    user: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    claim = _claim_for_user(db, claim_id, user)
    artifact = db.get(ImageryArtifact, artifact_id)
    if artifact is None or artifact.claim_id != claim.id:
        raise HTTPException(status_code=404, detail="Historical evidence artifact not found.")
    if artifact.state != "completed":
        raise HTTPException(status_code=409, detail="Only a completed historical observation can be reviewed.")
    before = {
        "verification_state": artifact.verification_state,
        "reviewer_notes": (artifact.provenance_json or {}).get("reviewer_notes"),
    }
    artifact.verification_state = payload.verification_state
    artifact.reviewed_by = user.id
    from datetime import datetime, timezone
    artifact.reviewed_at = datetime.now(timezone.utc)
    artifact.provenance_json = {**dict(artifact.provenance_json or {}), "reviewer_notes": payload.notes}
    record_audit(
        db, actor_id=user.id, action="fra_historical_evidence_reviewed",
        entity_type="imagery_artifact", entity_id=artifact.id, before=before,
        after={"verification_state": payload.verification_state, "reviewer_notes": payload.notes},
        request_id=_request_id(request),
    )
    db.commit()
    return {
        "id": str(artifact.id), "verification_state": artifact.verification_state,
        "reviewer_notes": payload.notes, "reviewed_at": artifact.reviewed_at,
    }
