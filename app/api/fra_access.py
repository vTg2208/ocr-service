"""Shared case visibility checks for authenticated FRA routes."""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser
from app.db.fra_models import FRAClaim
from app.db.models import Document
from app.services.fra_cases import can_view_case


def claim_for_user(db: Session, claim_id: uuid.UUID, user: AuthenticatedUser) -> FRAClaim:
    claim = db.get(FRAClaim, claim_id)
    if claim is None or not can_view_case(
        claim, user_id=user.id, privileged=user.role in {"reviewer", "admin"}
    ):
        raise HTTPException(status_code=404, detail="FRA claim not found.")
    return claim


def visible_claim_ids(user: AuthenticatedUser):
    """SQL scope for case-bound lists; privileged users need no restriction."""
    if user.role in {"reviewer", "admin"}:
        return None
    return select(FRAClaim.id).where(FRAClaim.submitted_by == user.id)


def document_for_user(db: Session, document_id: uuid.UUID, user: AuthenticatedUser) -> Document:
    document = db.get(Document, document_id)
    if document is None or (
        user.role not in {"reviewer", "admin"} and document.uploaded_by != user.id
    ):
        raise HTTPException(status_code=404, detail="Document not found.")
    return document
