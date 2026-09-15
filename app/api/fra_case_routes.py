"""Searchable native FRA case and case-reference endpoints."""

import hashlib
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user, require_reviewer
from app.config import get_settings
from app.db.fra_models import FRAEvidenceItem, GramSabha, RightsHolder
from app.db.models import Document
from app.db.session import get_db
from app.api.fra_access import claim_for_user
from app.services.audit import record_audit
from app.services.fra_cases import case_detail, case_summary, list_cases
from app.services.malware import ClamAVScanner, MalwareDetectedError, MalwareScannerUnavailable
from app.services.storage import create_storage
from app.utils.file_validation import FileValidationError, validate_upload


router = APIRouter(prefix="/api/fra", tags=["FRA cases"])
settings = get_settings()
DOCUMENT_TYPES = {
    "claim_form", "gram_sabha_record", "sdlc_record", "dlc_record",
    "title_right_record", "supporting_evidence", "map_sketch", "other",
}


@router.get("/cases")
def get_cases(
    status: str | None = None,
    right_type: str | None = None,
    district: str | None = None,
    block: str | None = None,
    village: str | None = None,
    query: str | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    privileged = user.role in {"reviewer", "admin"}
    items = list_cases(
        db, user_id=user.id, privileged=privileged, status=status,
        right_type=right_type, district=district, block=block, village=village, query=query,
    )
    return {"items": [case_summary(item) for item in items]}


@router.get("/cases/{claim_id}")
def get_case(
    claim_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    claim = claim_for_user(db, claim_id, user)
    privileged = user.role in {"reviewer", "admin"}
    return case_detail(db, claim, privileged=privileged)


@router.post("/cases/{claim_id}/supporting-documents", status_code=201)
async def upload_supporting_document(
    claim_id: uuid.UUID,
    request: Request,
    document_type: str = Form(...),
    source: str = Form(...),
    description: str = Form(...),
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    claim = claim_for_user(db, claim_id, user)
    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(status_code=422, detail="Choose a supported document type.")
    source, description = source.strip(), description.strip()
    if not source or len(source) > 100 or not description:
        raise HTTPException(status_code=422, detail="Source and description are required.")
    if source.casefold() == "spatial_evaluation_disposition":
        raise HTTPException(status_code=422, detail="Choose the office or person that supplied the document.")
    content = await file.read(settings.max_file_size_bytes + 1)
    try:
        validated = validate_upload(file.filename or "upload", content)
        ClamAVScanner(
            settings.clamav_host, settings.clamav_port,
            required=settings.malware_scan_required,
        ).scan(content)
    except FileValidationError as error:
        raise HTTPException(status_code=400, detail=error.message) from error
    except MalwareDetectedError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except MalwareScannerUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    storage = create_storage(settings)
    storage_key = storage.put(content, f".{validated.extension}")
    try:
        document = Document(
            uploaded_by=user.id,
            storage_key=storage_key,
            original_filename=validated.safe_filename,
            content_type={
                "pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "bmp": "image/bmp", "tif": "image/tiff",
                "tiff": "image/tiff",
            }[validated.extension],
            sha256=hashlib.sha256(content).hexdigest(),
            ocr_status="not_requested",
            idempotency_key=f"fra-case-evidence:{uuid.uuid4()}",
        )
        db.add(document)
        db.flush()
        evidence = FRAEvidenceItem(
            claim=claim,
            category="map" if document_type == "map_sketch" else "documentary",
            legal_role="submitted",
            source=source,
            description=description,
            document_id=document.id,
            provenance_json={
                "document_type": document_type,
                "entered_from": "fra_case_workspace",
            },
            verification_state="unverified",
            source_verified=False,
            created_by=user.id,
        )
        db.add(evidence)
        db.flush()
        record_audit(
            db, actor_id=user.id, action="fra_supporting_document_uploaded",
            entity_type="fra_claim", entity_id=claim.id,
            after={"evidence_id": str(evidence.id), "document_id": str(document.id)},
            request_id=getattr(request.state, "request_id", None),
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        storage.delete(storage_key)
        raise HTTPException(status_code=409, detail="Supporting document conflicts with existing data.") from error
    except Exception:
        db.rollback()
        storage.delete(storage_key)
        raise
    return {"id": str(evidence.id), "document_id": str(document.id)}


@router.get("/cases/{claim_id}/documents/{document_id}")
def view_case_document(
    claim_id: uuid.UUID,
    document_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    claim = claim_for_user(db, claim_id, user)
    linked = claim.document_id == document_id or any(
        item.document_id == document_id for item in claim.evidence_items
    )
    if not linked:
        raise HTTPException(status_code=404, detail="Case document not found.")
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Case document not found.")
    try:
        content = create_storage(settings).read(document.storage_key)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Case document not found.") from error
    filename = quote(document.original_filename, safe="._-")
    return Response(
        content=content,
        media_type=document.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f"inline; filename*=UTF-8''{filename}",
        },
    )


@router.get("/case-reference/rights-holders")
def get_rights_holders(
    _reviewer: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    holders = db.scalars(select(RightsHolder).order_by(RightsHolder.display_name, RightsHolder.id)).all()
    return {
        "items": [
            {
                "id": str(item.id), "display_name": item.display_name,
                "holder_type": item.holder_type,
                "gram_sabha_id": str(item.gram_sabha_id) if item.gram_sabha_id else None,
            }
            for item in holders
        ]
    }


@router.get("/case-reference/gram-sabhas")
def get_gram_sabhas(
    _reviewer: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    items = db.scalars(select(GramSabha).order_by(GramSabha.district, GramSabha.village, GramSabha.id)).all()
    return {
        "items": [
            {
                "id": str(item.id), "name": item.name, "village": item.village,
                "block": item.block, "district": item.district,
            }
            for item in items
        ]
    }
