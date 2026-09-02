"""Role-aware Tamil Nadu FRA operational dashboard endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user, require_reviewer
from app.db.session import get_db
from app.services.fra_dashboards import planner_dashboard, verifier_dashboard


router = APIRouter(prefix="/api/fra/dashboard", tags=["FRA operational dashboards"])


@router.get("/verifier")
def verifier_summary(
    district: str | None = Query(None, max_length=255), block: str | None = Query(None, max_length=255),
    village: str | None = Query(None, max_length=255),
    _user: AuthenticatedUser = Depends(require_reviewer), db: Session = Depends(get_db),
):
    return verifier_dashboard(db, district=district, block=block, village=village)


@router.get("/planner")
def planner_summary(
    district: str | None = Query(None, max_length=255), block: str | None = Query(None, max_length=255),
    village: str | None = Query(None, max_length=255),
    _user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db),
):
    return planner_dashboard(db, district=district, block=block, village=village)


__all__ = ["router"]
