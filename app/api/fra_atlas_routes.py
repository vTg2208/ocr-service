"""Protected Tamil Nadu FRA Atlas and village reference endpoints."""

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user
from app.db.session import get_db
from app.services.fra_atlas import (
    AtlasFilters,
    AtlasValidationError,
    atlas_features,
    atlas_summary,
    list_villages,
    village_detail,
)
from app.services.state_profiles import UnsupportedStateError


router = APIRouter(prefix="/api/fra", tags=["FRA Atlas"])


def _filters(
    *,
    state: str,
    district: str | None,
    block: str | None,
    village: str | None,
    tribal_group: str | None,
    right_type: str | None,
    status: str | None,
    year: int | None,
    layers: str,
) -> AtlasFilters:
    selected = tuple(item.strip() for item in layers.split(",") if item.strip())
    try:
        return AtlasFilters(
            state=state,
            district=district,
            block=block,
            village=village,
            tribal_group=tribal_group,
            right_type=right_type,
            status=status,
            year=year,
            layers=selected,
        )
    except UnsupportedStateError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": error.code, "state": error.state, "message": str(error)},
        ) from error
    except AtlasValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _query_filters(
    state: str = "TN",
    district: str | None = None,
    block: str | None = None,
    village: str | None = None,
    tribal_group: str | None = None,
    right_type: str | None = None,
    status: str | None = None,
    year: int | None = None,
    layers: str = "village,claim,title,asset",
) -> AtlasFilters:
    return _filters(
        state=state,
        district=district,
        block=block,
        village=village,
        tribal_group=tribal_group,
        right_type=right_type,
        status=status,
        year=year,
        layers=layers,
    )


@router.get("/atlas/features")
def get_atlas_features(
    filters: AtlasFilters = Depends(_query_filters),
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return atlas_features(db, filters, privileged=user.role in {"reviewer", "admin"})


@router.get("/atlas/summary")
def get_atlas_summary(
    filters: AtlasFilters = Depends(_query_filters),
    _user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return asdict(atlas_summary(db, filters))


def _village_dict(village, *, detailed: bool = False) -> dict:
    result = {
        "id": str(village.id),
        "state_code": village.state_code,
        "state_name": village.state_name,
        "district_code": village.district_code,
        "district_name": village.district_name,
        "block_code": village.block_code,
        "block_name": village.block_name,
        "village_code": village.village_code,
        "village_name": village.village_name,
        "tribal_groups": list(village.tribal_groups_json or []),
        "reference_version": village.reference_version,
        "synthetic": village.synthetic,
    }
    if detailed:
        result.update(
            {
                "boundary": village.boundary,
                "socioeconomic": dict(village.socioeconomic_json or {}),
                "provenance": dict(village.provenance_json or {}),
                "warning": (
                    "Synthetic sample data are not authoritative."
                    if village.synthetic
                    else None
                ),
            }
        )
    return result


@router.get("/villages")
def get_villages(
    state: str = "TN",
    district: str | None = None,
    block: str | None = None,
    village: str | None = None,
    tribal_group: str | None = None,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filters = _filters(
        state=state,
        district=district,
        block=block,
        village=village,
        tribal_group=tribal_group,
        right_type=None,
        status=None,
        year=None,
        layers="village",
    )
    return {"items": [_village_dict(item) for item in list_villages(db, filters)]}


@router.get("/villages/{village_id}")
def get_village(
    village_id: uuid.UUID,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    village = village_detail(db, village_id)
    if village is None:
        raise HTTPException(status_code=404, detail="FRA village profile not found.")
    return _village_dict(village, detailed=True)
