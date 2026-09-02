"""Searchable native FRA case and case-reference endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user, require_reviewer
from app.db.fra_models import FRAClaim, GramSabha, RightsHolder
from app.db.session import get_db
from app.services.fra_cases import can_view_case, case_detail, case_summary, list_cases


router = APIRouter(prefix="/api/fra", tags=["FRA cases"])


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
    claim = db.get(FRAClaim, claim_id)
    privileged = user.role in {"reviewer", "admin"}
    if claim is None or not can_view_case(claim, user_id=user.id, privileged=privileged):
        raise HTTPException(status_code=404, detail="FRA case not found.")
    return case_detail(db, claim, privileged=privileged)


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
