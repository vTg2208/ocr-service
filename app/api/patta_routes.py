"""Authenticated patta processing, parcel, claim, and admin APIs."""

import hashlib
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from app.api.auth import AuthenticatedUser, get_current_user, require_admin
from app.api.routes import ocr_endpoint
from app.config import get_settings
from app.db.models import Claim, ClaimConflict, Document, Notification, OCRResult, Parcel
from app.db.session import get_db
from app.models.land_mapping_models import ClaimRequest, ConflictUpdate, ResolveRequest
from app.services.audit import record_audit
from app.services.claim_eligibility import ClaimUnavailableError
from app.services.claim_service import ClaimService
from app.services.parcel_resolver import ParcelLookup, ParcelResolver, parcel_public_dict
from app.services.patta_extraction import extract_normalized_parcel_fields
from app.services.malware import ClamAVScanner, MalwareDetectedError, MalwareScannerUnavailable
from app.services.storage import create_storage
from app.utils.file_validation import FileValidationError, validate_upload

router = APIRouter(prefix="/api")
settings = get_settings()


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or str(uuid.uuid4())


def _resolution(db, fields: dict):
    return ParcelResolver(
        db, area_tolerance_percent=settings.area_tolerance_percent,
        automatic_match_confidence=settings.automatic_match_confidence,
    ).resolve(
        ParcelLookup(
            **{name: fields.get(name) or "" for name in (
                "state", "district", "taluk", "village", "survey_number", "subdivision_number"
            )},
            document_area_sqm=fields.get("document_area_sqm"),
            ocr_confidence=fields.get("confidence", 1.0),
            ambiguous_fields=fields.get("ambiguous_fields", []),
        )
    )


@router.post("/pattas/process")
async def process_patta(
    request: Request, file: UploadFile = File(...),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db),
):
    existing = db.scalar(select(Document).where(
        Document.uploaded_by == user.id, Document.idempotency_key == idempotency_key,
    ))
    if existing:
        result = db.scalar(select(OCRResult).where(OCRResult.document_id == existing.id))
        return result.structured_result_json["response"]
    content = await file.read()
    try:
        validated = validate_upload(file.filename, content)
    except FileValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    try:
        ClamAVScanner(
            settings.clamav_host, settings.clamav_port, required=settings.malware_scan_required,
        ).scan(content)
    except MalwareDetectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MalwareScannerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    storage = create_storage(settings)
    storage_key = storage.put(content, f".{validated.extension}")
    try:
        ocr = await ocr_endpoint(file=UploadFile(filename=validated.safe_filename, file=__import__('io').BytesIO(content)), prompt=None)
        extracted = extract_normalized_parcel_fields(ocr.text, ocr.confidence)
        resolution = _resolution(db, extracted)
        document = Document(
            uploaded_by=user.id, storage_key=storage_key,
            original_filename=validated.safe_filename,
            content_type=file.content_type or "application/octet-stream",
            sha256=hashlib.sha256(content).hexdigest(), ocr_status="completed",
            idempotency_key=idempotency_key,
        )
        db.add(document); db.flush()
        response = {
            "document_id": str(document.id), "extracted_fields": extracted,
            "resolution": resolution.model_dump(),
        }
        valid_ids = []
        if resolution.parcel:
            valid_ids.append(resolution.parcel["id"])
        valid_ids.extend(item["id"] for item in resolution.alternatives)
        db.add(OCRResult(
            document_id=document.id, raw_text=ocr.text,
            overall_confidence=extracted["confidence"],
            structured_result_json={"response": response, "valid_parcel_ids": valid_ids},
            extractor_version="parcel-normalizer-v1",
        ))
        record_audit(
            db, actor_id=user.id, action="document_uploaded", entity_type="document",
            entity_id=document.id, after={"ocr_status": "completed"}, request_id=_request_id(request),
        )
        db.commit()
        return response
    except Exception:
        db.rollback()
        storage.delete(storage_key)
        raise


@router.post("/parcels/resolve")
def resolve_parcel(
    payload: ResolveRequest, request: Request,
    user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db),
):
    document = db.get(Document, payload.document_id)
    if document is None or document.uploaded_by != user.id:
        raise HTTPException(status_code=404, detail="Document not found.")
    result = _resolution(db, payload.model_dump())
    ocr_result = db.scalar(select(OCRResult).where(OCRResult.document_id == document.id))
    valid_ids = ([result.parcel["id"]] if result.parcel else []) + [item["id"] for item in result.alternatives]
    structured = dict(ocr_result.structured_result_json)
    structured["valid_parcel_ids"] = valid_ids
    structured["corrected_fields"] = payload.model_dump(mode="json")
    ocr_result.structured_result_json = structured
    record_audit(
        db, actor_id=user.id, action="extraction_corrected", entity_type="document",
        entity_id=document.id, after=payload.model_dump(mode="json"), request_id=_request_id(request),
    )
    db.commit()
    return result.model_dump()


@router.get("/parcels/{parcel_id}")
def get_parcel(parcel_id: uuid.UUID, _user=Depends(get_current_user), db: Session = Depends(get_db)):
    parcel = db.get(Parcel, parcel_id)
    if parcel is None:
        raise HTTPException(status_code=404, detail="Parcel not found.")
    return parcel_public_dict(parcel)


@router.post("/claims")
def submit_claim(
    payload: ClaimRequest, request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db),
):
    try:
        with db.begin_nested():
            response = ClaimService(
                db, overlap_min_sqm=settings.overlap_min_sqm,
                overlap_min_percent=settings.overlap_min_percent,
            ).submit(
                claimant_id=user.id, document_id=payload.document_id,
                parcel_id=payload.parcel_id, confirmed_fields=payload.confirmed_fields,
                idempotency_key=idempotency_key, request_id=_request_id(request),
            )
        db.commit()
        return response
    except ClaimUnavailableError as exc:
        db.rollback()
        record_audit(
            db, actor_id=user.id, action="claim_rejected", entity_type="parcel",
            entity_id=payload.parcel_id,
            after={"reason": exc.reason, "blocking_claim_id": str(exc.blocking_claim_id)},
            request_id=_request_id(request),
        )
        db.commit()
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "message": "This land is already claimed.",
                "reason": exc.reason,
            },
        )
    except IntegrityError:
        db.rollback()
        record_audit(
            db, actor_id=user.id, action="claim_rejected", entity_type="parcel",
            entity_id=payload.parcel_id, after={"reason": "same_parcel"},
            request_id=_request_id(request),
        )
        db.commit()
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "message": "This land is already claimed.",
                "reason": "same_parcel",
            },
        )
    except PermissionError as exc:
        db.rollback(); raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback(); raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/claims/mine")
def my_claims(user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    claims = db.scalars(select(Claim).where(Claim.claimant_id == user.id).order_by(Claim.submitted_at.desc()))
    return [{"id": str(item.id), "parcel_id": str(item.parcel_id), "status": item.status,
             "submitted_at": item.submitted_at.isoformat()} for item in claims]


@router.get("/notifications/mine")
def my_notifications(user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    notifications = db.scalars(select(Notification).where(
        Notification.user_id == user.id
    ).order_by(Notification.created_at.desc()))
    return [{
        "id": str(item.id), "type": item.notification_type, "message": item.message,
        "entity_type": item.entity_type, "entity_id": str(item.entity_id),
        "created_at": item.created_at.isoformat(), "read": item.read_at is not None,
    } for item in notifications]


def _admin_conflict(conflict: ClaimConflict, db: Session) -> dict:
    a, b = db.get(Claim, conflict.claim_a_id), db.get(Claim, conflict.claim_b_id)
    parcel_a, parcel_b = db.get(Parcel, a.parcel_id), db.get(Parcel, b.parcel_id)
    ocr_a = db.scalar(select(OCRResult).where(OCRResult.document_id == a.document_id))
    ocr_b = db.scalar(select(OCRResult).where(OCRResult.document_id == b.document_id))
    return {
        "id": str(conflict.id), "type": conflict.conflict_type, "status": conflict.status,
        "claim_a": {"id": str(a.id), "claimant_id": str(a.claimant_id), "document_id": str(a.document_id), "parcel": parcel_public_dict(parcel_a), "confirmed_fields": a.confirmed_fields_json, "evidence": ocr_a.structured_result_json.get("response", {}).get("extracted_fields", {}).get("evidence", {})},
        "claim_b": {"id": str(b.id), "claimant_id": str(b.claimant_id), "document_id": str(b.document_id), "parcel": parcel_public_dict(parcel_b), "confirmed_fields": b.confirmed_fields_json, "evidence": ocr_b.structured_result_json.get("response", {}).get("extracted_fields", {}).get("evidence", {})},
        "overlap_area_sqm": float(conflict.overlap_area_sqm) if conflict.overlap_area_sqm is not None else None,
        "overlap_percent": float(conflict.overlap_percent) if conflict.overlap_percent is not None else None,
        "resolution_notes": conflict.resolution_notes,
        "resolution_history": conflict.resolution_history_json,
        "resolved_at": conflict.resolved_at.isoformat() if conflict.resolved_at else None,
    }


@router.get("/admin/conflicts")
def admin_conflicts(_admin=Depends(require_admin), db: Session = Depends(get_db)):
    return [_admin_conflict(item, db) for item in db.scalars(select(ClaimConflict).order_by(ClaimConflict.created_at))]


@router.get("/admin/conflicts/{conflict_id}")
def admin_conflict(conflict_id: uuid.UUID, _admin=Depends(require_admin), db: Session = Depends(get_db)):
    conflict = db.get(ClaimConflict, conflict_id)
    if conflict is None: raise HTTPException(status_code=404, detail="Conflict not found.")
    return _admin_conflict(conflict, db)


@router.patch("/admin/conflicts/{conflict_id}")
def update_conflict(
    conflict_id: uuid.UUID, payload: ConflictUpdate, request: Request,
    admin: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db),
):
    conflict = db.get(ClaimConflict, conflict_id)
    if conflict is None: raise HTTPException(status_code=404, detail="Conflict not found.")
    before = {"status": conflict.status, "resolution_notes": conflict.resolution_notes}
    conflict.status, conflict.resolution_notes = payload.status, payload.resolution_notes
    conflict.resolved_at = (
        datetime.now(timezone.utc) if payload.status in {"resolved", "dismissed"} else None
    )
    history = list(conflict.resolution_history_json or [])
    history.append({"actor_id": str(admin.id), **payload.model_dump()})
    conflict.resolution_history_json = history
    record_audit(
        db, actor_id=admin.id, action="conflict_resolved", entity_type="claim_conflict",
        entity_id=conflict.id, before=before, after=payload.model_dump(), request_id=_request_id(request),
    )
    db.commit()
    return _admin_conflict(conflict, db)
