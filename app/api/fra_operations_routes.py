"""Protected model-registry and persistent-job operations."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user, require_admin, require_reviewer
from app.db.fra_completion_models import ModelVersion, ProcessingJob
from app.db.session import get_db
from app.models.fra_completion_schemas import ModelVersionCreate
from app.services.audit import record_audit
from app.services.model_gateway import (
    ModelRegistrationError,
    activate_model,
    register_model,
)
from app.services.processing_jobs import JobStateError, retry_job


router = APIRouter(prefix="/api/fra", tags=["FRA operations"])


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")


def _commit(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=message) from error


def _model_dict(model: ModelVersion) -> dict:
    return {
        "id": str(model.id),
        "task": model.task,
        "name": model.name,
        "version": model.version,
        "adapter_type": model.adapter_type,
        "framework": model.framework,
        "status": model.status,
        "label_map": dict(model.label_map_json or {}),
        "metrics": dict(model.metrics_json or {}),
        "ready": model.configuration_json.get("ready") is True,
        "registered_at": model.registered_at.isoformat(),
        "activated_at": model.activated_at.isoformat() if model.activated_at else None,
    }


def _job_dict(job: ProcessingJob, *, detailed: bool = False) -> dict:
    result = {
        "id": str(job.id),
        "task_type": job.task_type,
        "entity_type": job.entity_type,
        "entity_id": str(job.entity_id),
        "state": job.state,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }
    if detailed:
        result["result"] = dict(job.result_json or {})
    return result


def _job_or_404(db: Session, job_id: uuid.UUID) -> ProcessingJob:
    job = db.get(ProcessingJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Processing job not found.")
    return job


def _can_view_job(user: AuthenticatedUser, job: ProcessingJob) -> bool:
    return user.role in {"reviewer", "admin"} or job.requested_by == user.id


@router.post("/models", status_code=201)
def create_model_version(
    payload: ModelVersionCreate,
    request: Request,
    user: AuthenticatedUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        model = register_model(
            db,
            task=payload.task,
            name=payload.name,
            version=payload.version,
            adapter_type=payload.adapter_type,
            actor_id=user.id,
            framework=payload.framework,
            artifact_uri=payload.artifact_uri,
            checksum=payload.checksum,
            label_map=payload.label_map,
            metrics=payload.metrics,
            configuration=payload.configuration,
            request_id=_request_id(request),
        )
    except ModelRegistrationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _commit(db, "That model task, name, and version is already registered.")
    return _model_dict(model)


@router.get("/models")
def list_models(
    task: str | None = None,
    status: str | None = None,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = select(ModelVersion)
    if task:
        statement = statement.where(ModelVersion.task == task)
    if status:
        statement = statement.where(ModelVersion.status == status)
    models = db.scalars(statement.order_by(ModelVersion.task, ModelVersion.name, ModelVersion.version)).all()
    return {"items": [_model_dict(model) for model in models]}


@router.post("/models/{model_id}/activate")
def activate_model_version(
    model_id: uuid.UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    model = db.get(ModelVersion, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Model version not found.")
    try:
        activate_model(db, model, actor_id=user.id, request_id=_request_id(request))
    except ModelRegistrationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _commit(db, "The model activation conflicted with another update.")
    return _model_dict(model)


@router.get("/jobs")
def list_jobs(
    state: str | None = None,
    task_type: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = select(ProcessingJob)
    if user.role not in {"reviewer", "admin"}:
        statement = statement.where(ProcessingJob.requested_by == user.id)
    if state:
        statement = statement.where(ProcessingJob.state == state)
    if task_type:
        statement = statement.where(ProcessingJob.task_type == task_type)
    jobs = db.scalars(
        statement.order_by(ProcessingJob.created_at.desc(), ProcessingJob.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {"items": [_job_dict(job) for job in jobs], "offset": offset, "limit": limit}


@router.get("/jobs/{job_id}")
def get_job(
    job_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _job_or_404(db, job_id)
    if not _can_view_job(user, job):
        raise HTTPException(status_code=404, detail="Processing job not found.")
    return _job_dict(job, detailed=True)


@router.post("/jobs/{job_id}/retry")
def retry_processing_job(
    job_id: uuid.UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    job = _job_or_404(db, job_id)
    try:
        retry_job(db, job)
    except JobStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    record_audit(
        db,
        actor_id=user.id,
        action="fra_processing_job_retried",
        entity_type="processing_job",
        entity_id=job.id,
        after={"state": job.state},
        request_id=_request_id(request),
    )
    _commit(db, "The job changed while it was being retried.")
    return _job_dict(job)
