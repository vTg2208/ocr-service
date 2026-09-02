"""Protected, privacy-aware endpoints for the Tamil Nadu FRA archive."""

import uuid

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user, require_reviewer
from app.db.fra_completion_models import FRAArchiveRecord, FRAImportBatch
from app.db.models import Document
from app.db.session import get_db
from app.config import get_settings
from app.models.fra_completion_schemas import (
    FRAArchiveBatchUploadResponse,
    FRAArchiveRecordCreate,
    FRAArchiveReview,
    FRAImportBatchCreate,
)
from app.services.fra_document_intake import ArchiveUpload, ingest_archive_batch
from app.services.malware import ClamAVScanner
from app.services.storage import create_storage
from app.services.fra_archive import (
    ArchiveConflictError,
    ArchiveValidationError,
    create_archive_record,
    create_import_batch,
    promote_archive_record,
    review_archive_record,
    search_archive,
)
from app.services.processing_jobs import enqueue_job
from app.services.state_profiles import UnsupportedStateError


router = APIRouter(prefix="/api/fra/archive", tags=["FRA archive"])
settings = get_settings()


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")


def _commit(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=message) from error


def _unsupported(error: UnsupportedStateError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": error.code, "state": error.state, "message": str(error)},
    )


def _record_or_404(db: Session, record_id: uuid.UUID) -> FRAArchiveRecord:
    record = db.get(FRAArchiveRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="FRA archive record not found.")
    return record


def _record_summary(record: FRAArchiveRecord) -> dict:
    return {
        "id": str(record.id),
        "batch_id": str(record.batch_id),
        "legacy_reference": record.legacy_reference,
        "state_code": record.state_code,
        "claim_number": record.claim_number,
        "holder_display_name": record.holder_display_name,
        "district": record.district,
        "block": record.block,
        "village": record.village,
        "right_type": record.right_type,
        "claim_status": record.claim_status,
        "claim_year": record.claim_year,
        "review_state": record.review_state,
        "revision": record.revision,
        "synthetic": record.synthetic,
        "promoted_claim_id": str(record.promoted_claim_id) if record.promoted_claim_id else None,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


@router.post(
    "/batch-upload",
    status_code=202,
    response_model=FRAArchiveBatchUploadResponse,
)
async def upload_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    source_office: str = Form(..., min_length=1, max_length=255),
    district: str = Form(..., min_length=1, max_length=255),
    idempotency_key: str = Header(..., alias="Idempotency-Key", max_length=255),
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if len(files) > settings.fra_archive_max_batch_files:
        raise HTTPException(
            status_code=413,
            detail=f"A batch can contain at most {settings.fra_archive_max_batch_files} files.",
        )
    uploads = []
    total_bytes = 0
    read_limit = settings.max_file_size_bytes + 1
    for upload in files:
        content = await upload.read(read_limit)
        total_bytes += len(content)
        uploads.append(ArchiveUpload(upload.filename or "upload", upload.content_type, content))
    if total_bytes > settings.fra_archive_max_batch_total_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The archive batch is too large.")
    try:
        result = ingest_archive_batch(
            db,
            files=uploads,
            source_office=source_office,
            district=district,
            actor_id=user.id,
            idempotency_key=idempotency_key,
            storage=create_storage(settings),
            scanner=ClamAVScanner(
                settings.clamav_host,
                settings.clamav_port,
                required=settings.malware_scan_required,
            ),
            request_id=_request_id(request),
        )
    except ArchiveValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _commit(db, "The archive batch conflicts with an existing upload.")
    return result


@router.post("/batches", status_code=201)
def create_batch(
    payload: FRAImportBatchCreate,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    provenance = dict(payload.provenance)
    provenance.setdefault("source", payload.source_label)
    provenance.setdefault("synthetic", payload.synthetic)
    try:
        batch = create_import_batch(
            db,
            source_label=payload.source_label,
            state=payload.state,
            actor_id=user.id,
            idempotency_key=payload.idempotency_key,
            synthetic=payload.synthetic,
            provenance=provenance,
            request_id=_request_id(request),
        )
    except UnsupportedStateError as error:
        raise _unsupported(error) from error
    except ArchiveValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _commit(db, "An import batch with that idempotency key already exists.")
    return {
        "id": str(batch.id),
        "source_label": batch.source_label,
        "state_code": batch.state_code,
        "status": batch.status,
        "record_count": batch.record_count,
        "processed_count": batch.processed_count,
        "failed_count": batch.failed_count,
        "synthetic": batch.synthetic,
    }


@router.post("/records", status_code=201)
def create_record(
    payload: FRAArchiveRecordCreate,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = db.get(FRAImportBatch, payload.batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="FRA import batch not found.")
    if db.get(Document, payload.document_id) is None:
        raise HTTPException(status_code=404, detail="Archive document not found.")
    try:
        record = create_archive_record(
            db,
            batch=batch,
            document_id=payload.document_id,
            legacy_reference=payload.legacy_reference,
            actor_id=user.id,
            synthetic=payload.synthetic,
            provenance=payload.provenance or None,
            request_id=_request_id(request),
        )
        job = enqueue_job(
            db,
            task_type="archive_extract",
            entity_type="archive_record",
            entity_id=record.id,
            actor_id=user.id,
            idempotency_key=payload.idempotency_key or f"extract:{record.legacy_reference}",
            payload={
                "record_id": str(record.id),
                "manifest": payload.extraction_manifest or {},
            },
        )
    except UnsupportedStateError as error:
        raise _unsupported(error) from error
    except ArchiveConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ArchiveValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _commit(db, "The archive record conflicts with an existing record.")
    return {**_record_summary(record), "processing_job_id": str(job.id)}


@router.get("/records")
def list_records(
    query: str = Query(default="", max_length=255),
    state_code: str | None = None,
    district: str | None = None,
    block: str | None = None,
    village: str | None = None,
    right_type: str | None = None,
    claim_status: str | None = None,
    review_state: str | None = None,
    claim_year: int | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filters = {
        "state_code": state_code,
        "district": district,
        "block": block,
        "village": village,
        "right_type": right_type,
        "claim_status": claim_status,
        "review_state": review_state,
        "claim_year": claim_year,
    }
    try:
        records = search_archive(db, query=query, filters=filters, offset=offset, limit=limit)
    except ArchiveValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"items": [_record_summary(record) for record in records], "offset": offset, "limit": limit}


@router.get("/records/{record_id}")
def get_record(
    record_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = _record_or_404(db, record_id)
    privileged = user.role in {"reviewer", "admin"}
    extractions = [
        {
            "id": str(run.id),
            "ocr_model_version": run.ocr_model_version,
            "entity_model_version": run.entity_model_version,
            "standardized_fields": dict(run.standardized_json or {}),
            "field_evidence": dict(run.field_evidence_json or {}),
            "confidence": float(run.overall_confidence) if run.overall_confidence is not None else None,
            "processing_time_ms": run.processing_time_ms,
            "provenance": dict(run.provenance_json or {}),
            "created_at": run.created_at.isoformat(),
            **({"raw_text": run.raw_text} if privileged else {}),
        }
        for run in record.extraction_runs
    ]
    return {
        **_record_summary(record),
        "reviewed_fields": dict(record.reviewed_fields_json or {}),
        "provenance": dict(record.provenance_json or {}),
        "extraction_runs": extractions,
        "warning": "Synthetic sample data" if record.synthetic else None,
    }


@router.post("/records/{record_id}/review")
def review_record(
    record_id: uuid.UUID,
    payload: FRAArchiveReview,
    request: Request,
    user: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    record = _record_or_404(db, record_id)
    try:
        review_archive_record(
            db,
            record,
            reviewed_fields=payload.reviewed_fields,
            reviewer_id=user.id,
            expected_revision=payload.expected_revision,
            request_id=_request_id(request),
        )
    except ArchiveConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ArchiveValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _commit(db, "The archive record changed during review.")
    return _record_summary(record)


@router.post("/records/{record_id}/promote", status_code=201)
def promote_record(
    record_id: uuid.UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    record = _record_or_404(db, record_id)
    try:
        claim = promote_archive_record(
            db, record, actor_id=user.id, request_id=_request_id(request)
        )
    except ArchiveConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ArchiveValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _commit(db, "The reviewed archive record conflicts with an existing FRA claim.")
    return {
        "record_id": str(record.id),
        "claim_id": str(claim.id),
        "claim_number": claim.claim_number,
        "right_type": claim.right_type,
        "status": claim.status,
    }
