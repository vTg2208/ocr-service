"""Protected legacy-claim intake and native FRA promotion routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user, require_reviewer
from app.db.fra_operational_models import FRAIntakeItem
from app.db.models import Claim
from app.db.session import get_db
from app.models.fra_intake_schemas import FRAIntakePromote, FRAIntakeUpdate
from app.services.fra_claims import FRAClaimValidationError
from app.services.fra_intake import IntakeConflictError, promote_intake, update_intake


router = APIRouter(prefix="/api/fra/intake", tags=["FRA intake"])


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="The FRA intake conflicts with existing data.") from error


def _item_dict(item: FRAIntakeItem) -> dict:
    legacy = item.legacy_claim
    parcel = legacy.parcel
    return {
        "id": str(item.id),
        "legacy_claim_id": str(item.legacy_claim_id),
        "legacy_status": legacy.status,
        "state": item.state,
        "promoted_claim_id": str(item.promoted_claim_id) if item.promoted_claim_id else None,
        "revision": item.revision,
        "triage": dict(item.triage_json or {}),
        "reasons": list(item.reasons_json or []),
        "location": {
            "district": parcel.district,
            "block": parcel.taluk,
            "village": parcel.village,
            "survey_number": parcel.survey_number,
            "subdivision_number": parcel.subdivision_number,
        },
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _visible_item(db: Session, item_id: uuid.UUID, user: AuthenticatedUser) -> FRAIntakeItem:
    item = db.get(FRAIntakeItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="FRA intake item not found.")
    if user.role not in {"reviewer", "admin"} and item.legacy_claim.claimant_id != user.id:
        raise HTTPException(status_code=404, detail="FRA intake item not found.")
    return item


@router.get("")
def list_intake(
    state: str | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = select(FRAIntakeItem).join(
        Claim, FRAIntakeItem.legacy_claim_id == Claim.id
    )
    if user.role not in {"reviewer", "admin"}:
        statement = statement.where(Claim.claimant_id == user.id)
    if state:
        statement = statement.where(FRAIntakeItem.state == state)
    items = db.scalars(statement.order_by(FRAIntakeItem.created_at.desc())).all()
    return {"items": [_item_dict(item) for item in items]}


@router.get("/{item_id}")
def get_intake(
    item_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _item_dict(_visible_item(db, item_id, user))


@router.patch("/{item_id}")
def patch_intake(
    item_id: uuid.UUID,
    payload: FRAIntakeUpdate,
    request: Request,
    reviewer: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    item = _visible_item(db, item_id, reviewer)
    try:
        update_intake(
            db,
            item,
            target_state=payload.target_state,
            expected_revision=payload.expected_revision,
            reasons=payload.reasons,
            triage=payload.triage,
            actor_id=reviewer.id,
            request_id=_request_id(request),
        )
    except IntakeConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _commit(db)
    return _item_dict(item)


@router.post("/{item_id}/promote", status_code=201)
def promote_intake_item(
    item_id: uuid.UUID,
    payload: FRAIntakePromote,
    request: Request,
    reviewer: AuthenticatedUser = Depends(require_reviewer),
    db: Session = Depends(get_db),
):
    item = _visible_item(db, item_id, reviewer)
    try:
        claim = promote_intake(
            db,
            item,
            right_type=payload.right_type,
            rights_holder_id=payload.rights_holder_id,
            gram_sabha_id=payload.gram_sabha_id,
            expected_revision=payload.expected_revision,
            actor_id=reviewer.id,
            request_id=_request_id(request),
        )
    except IntakeConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except FRAClaimValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _commit(db)
    return {
        "intake_id": str(item.id),
        "claim_id": str(claim.id),
        "claim_number": claim.claim_number,
        "right_type": claim.right_type,
        "status": claim.status,
        "legacy_claim_id": str(claim.legacy_claim_id),
        "intake_state": item.state,
        "revision": item.revision,
    }
