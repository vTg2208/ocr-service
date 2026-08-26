"""Authenticated registry views for claimed parcels and their source pattas."""

import uuid
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import selectinload
from starlette.responses import Response

from app.api.auth import AuthenticatedUser, get_current_user
from app.config import get_settings
from app.db.models import Claim, Document
from app.db.session import get_db
from app.services.audit import record_audit
from app.services.storage import create_storage
from app.services.parcel_resolver import parcel_public_dict


router = APIRouter(prefix="/api/claims")
settings = get_settings()


def _request_id(request: Request) -> str:
    return (
        getattr(request.state, "request_id", None)
        or request.headers.get("X-Request-ID")
        or str(uuid.uuid4())
    )


@router.get("/registry")
def claimed_land_registry(
    _user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    claims = list(db.scalars(
        select(Claim)
        .where(Claim.status.in_(("pending", "matched", "conflicting")))
        .options(selectinload(Claim.parcel), selectinload(Claim.document))
        .order_by(Claim.submitted_at, Claim.id)
    ))
    total_area = sum(
        (claim.parcel.official_area_sqm or Decimal("0") for claim in claims),
        Decimal("0"),
    )
    return {
        "summary": {
            "claimed_parcel_count": len(claims),
            "claimed_official_area_sqm": float(total_area),
        },
        "claims": [
            {
                "claim_id": str(claim.id),
                "status": claim.status,
                "submitted_at": claim.submitted_at.isoformat(),
                "parcel": parcel_public_dict(claim.parcel),
                "document": {
                    "filename": claim.document.original_filename,
                    "content_type": claim.document.content_type,
                    "view_url": f"/api/claims/{claim.id}/patta",
                },
            }
            for claim in claims
        ],
    }


@router.get("/{claim_id}/patta")
def view_claim_patta(
    claim_id: uuid.UUID,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Registered claim not found.")
    document = db.get(Document, claim.document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Patta document not found.")
    try:
        content = create_storage(settings).read(document.storage_key)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Patta document not found.") from exc

    record_audit(
        db, actor_id=user.id, action="patta_viewed", entity_type="claim",
        entity_id=claim.id, after={"document_id": str(document.id)},
        request_id=_request_id(request),
    )
    db.commit()
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
