"""Protected staging and publication routes for FRA reference vectors."""

from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user, require_reviewer
from app.config import get_settings
from app.db.fra_operational_models import SpatialImportBatch
from app.db.session import get_db
from app.models.fra_geospatial_schemas import SpatialImportPreview, SpatialImportSummary
from app.services.fra_geospatial_import import (
    SpatialImportValidationError,
    publish_spatial_import,
    reader_for_filename,
    stage_spatial_import,
)
from app.services.malware import ClamAVScanner, MalwareDetectedError, MalwareScannerUnavailable
from app.services.storage import create_storage


router = APIRouter(prefix="/api/fra/geospatial", tags=["FRA geospatial imports"])
settings = get_settings()
ALLOWED_SUFFIXES = {".geojson", ".json", ".zip", ".gpkg", ".kml"}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")


def _batch_or_404(db: Session, batch_id: uuid.UUID) -> SpatialImportBatch:
    batch = db.get(SpatialImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Spatial import not found.")
    return batch


def _summary(batch: SpatialImportBatch) -> dict:
    return {
        "id": str(batch.id), "dataset_kind": batch.dataset_kind,
        "source_authority": batch.source_authority, "source_version": batch.source_version,
        "state": batch.state, "declared_crs": batch.declared_crs,
        "detected_crs": batch.detected_crs, "record_count": batch.record_count,
        "valid_count": batch.valid_count, "invalid_count": batch.invalid_count,
        "repaired_count": batch.repaired_count, "duplicate_count": batch.duplicate_count,
        "synthetic": batch.synthetic,
        "classification": batch.provenance_json.get("classification"),
    }


@router.post("/imports", status_code=202, response_model=SpatialImportSummary)
async def create_import(
    request: Request,
    file: UploadFile = File(...),
    dataset_kind: str = Form(..., min_length=1, max_length=64),
    source_authority: str = Form(..., min_length=1, max_length=255),
    source_version: str = Form(..., min_length=1, max_length=100),
    declared_crs: str = Form("EPSG:4326", min_length=1, max_length=100),
    synthetic: bool = Form(False),
    idempotency_key: str = Header(..., alias="Idempotency-Key", max_length=255),
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filename = Path(file.filename or "upload").name
    suffix = Path(filename).suffix.casefold()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="Unsupported vector dataset format.")
    content = await file.read(settings.max_file_size_bytes + 1)
    if len(content) > settings.max_file_size_bytes:
        raise HTTPException(status_code=413, detail="The vector dataset exceeds the file size limit.")
    scanner = ClamAVScanner(
        settings.clamav_host, settings.clamav_port, required=settings.malware_scan_required,
    )
    try:
        scanner.scan(content)
    except MalwareDetectedError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except MalwareScannerUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    storage = create_storage(settings)
    storage_key = storage.put(content, suffix)
    try:
        batch = stage_spatial_import(
            db, content=content, filename=filename, dataset_kind=dataset_kind,
            source_authority=source_authority, source_version=source_version,
            declared_crs=declared_crs, actor_id=user.id,
            idempotency_key=idempotency_key, synthetic=synthetic,
            storage_key=storage_key, request_id=_request_id(request),
            reader=reader_for_filename(filename),
        )
        db.commit()
    except SpatialImportValidationError as error:
        db.rollback(); storage.delete(storage_key)
        raise HTTPException(status_code=422, detail=str(error)) from error
    except IntegrityError as error:
        db.rollback(); storage.delete(storage_key)
        raise HTTPException(status_code=409, detail="The spatial import conflicts with existing source records.") from error
    except Exception:
        db.rollback(); storage.delete(storage_key); raise
    return _summary(batch)


@router.get("/imports/{batch_id}", response_model=SpatialImportPreview)
def preview_import(
    batch_id: uuid.UUID,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = _batch_or_404(db, batch_id)
    return {
        **_summary(batch),
        "errors": list((batch.error_summary_json or {}).get("features", [])),
        "features": [
            {
                "id": str(feature.id), "source_record_id": feature.source_record_id,
                "geometry": feature.geometry, "properties": dict(feature.properties_json or {}),
                "repaired": bool((feature.provenance_json or {}).get("repaired")),
            }
            for feature in batch.features[:200]
        ],
    }


@router.post("/imports/{batch_id}/publish", response_model=SpatialImportSummary)
def publish_import(
    batch_id: uuid.UUID,
    request: Request,
    user: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    batch = _batch_or_404(db, batch_id)
    try:
        publish_spatial_import(
            db, batch, reviewer_id=user.id, request_id=_request_id(request)
        )
        db.commit()
    except SpatialImportValidationError as error:
        db.rollback(); raise HTTPException(status_code=422, detail=str(error)) from error
    return _summary(batch)
