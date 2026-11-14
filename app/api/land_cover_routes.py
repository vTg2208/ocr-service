"""Owner/staff-scoped satellite analysis, artifacts, reports, and human review."""

from datetime import date, datetime, timedelta, timezone
import html
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user, require_reviewer
from app.db.community_models import PlotSubmission
from app.db.land_cover_models import LandCoverAnalysis
from app.db.session import get_db
from app.services import land_cover as service
from app.services.audit import record_audit
from app.services.land_cover_raster import LandCoverError
from app.services.storage import create_storage
from app.config import get_settings


router = APIRouter(tags=["Satellite land cover"])


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date = Field(default_factory=lambda: datetime.now(timezone.utc).date()-timedelta(days=90))
    end_date: date = Field(default_factory=lambda: datetime.now(timezone.utc).date()-timedelta(days=1))


class AnalysisReview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    state: Literal["verified", "rejected"]
    note: str = Field(min_length=1, max_length=4000)
    expected_revision: int = Field(ge=1)


def private(data, status_code=200):
    return JSONResponse(data, status_code=status_code, headers={"Cache-Control": "no-store"})


def find_analysis(db, identifier, user):
    analysis = db.get(LandCoverAnalysis, identifier)
    if analysis is None:
        raise HTTPException(404, "Analysis not found.")
    service.target_for_actor(db, analysis.target_type, analysis.target_id, user)
    return analysis


def collection(db, target_type, target_id, user):
    service.target_for_actor(db, target_type, target_id, user)
    rows = db.scalars(select(LandCoverAnalysis).where(LandCoverAnalysis.target_type == target_type,
        LandCoverAnalysis.target_id == target_id).order_by(LandCoverAnalysis.created_at.desc()).limit(20))
    return private({"items": [service.analysis_json(db, row) for row in rows], "warning": service.WARNING})


def create(db, target_type, target_id, user, payload):
    target = service.target_for_actor(db, target_type, target_id, user)
    try:
        row = service.request_analysis(db, target_type=target_type, target=target, actor_id=user.id,
            start_date=payload.start_date, end_date=payload.end_date)
        db.commit()
    except LandCoverError as error:
        db.rollback()
        raise HTTPException(422, str(error)) from error
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "An identical request was just saved; refresh the analysis list.") from error
    return private(service.analysis_json(db, row), status_code=202)


@router.get("/api/fra/claims/{claim_id}/land-cover")
def claim_analyses(claim_id: uuid.UUID, user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return collection(db, "fra_claim", claim_id, user)


@router.post("/api/fra/claims/{claim_id}/land-cover", status_code=202)
def analyse_claim(claim_id: uuid.UUID, payload: AnalysisRequest, user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return create(db, "fra_claim", claim_id, user, payload)


def approved_submission(db, plot_id, user):
    from app.api.user_portal_routes import own_plot
    own_plot(db, plot_id, user)
    submission = db.scalar(select(PlotSubmission).where(PlotSubmission.plot_id == plot_id,
        PlotSubmission.status == "approved"))
    if submission is None:
        raise HTTPException(422, "Submit the plot and obtain staff boundary approval first.")
    return submission


@router.get("/api/user/plots/{plot_id}/land-cover")
def plot_analyses(plot_id: uuid.UUID, user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    submission = approved_submission(db, plot_id, user)
    return collection(db, "plot_submission", submission.id, user)


@router.post("/api/user/plots/{plot_id}/land-cover", status_code=202)
def analyse_plot(plot_id: uuid.UUID, payload: AnalysisRequest, user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    submission = approved_submission(db, plot_id, user)
    return create(db, "plot_submission", submission.id, user, payload)


@router.get("/api/staff/portal/submissions/{submission_id}/land-cover")
def submission_analyses(submission_id: uuid.UUID, user: AuthenticatedUser = Depends(require_reviewer), db: Session = Depends(get_db)):
    return collection(db, "plot_submission", submission_id, user)


@router.post("/api/staff/portal/submissions/{submission_id}/land-cover", status_code=202)
def analyse_submission(submission_id: uuid.UUID, payload: AnalysisRequest, user: AuthenticatedUser = Depends(require_reviewer), db: Session = Depends(get_db)):
    return create(db, "plot_submission", submission_id, user, payload)


@router.get("/api/land-cover/{analysis_id}")
def analysis_detail(analysis_id: uuid.UUID, user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return private(service.analysis_json(db, find_analysis(db, analysis_id, user)))


@router.post("/api/land-cover/{analysis_id}/review")
def review(analysis_id: uuid.UUID, payload: AnalysisReview, user: AuthenticatedUser = Depends(require_reviewer), db: Session = Depends(get_db)):
    row = find_analysis(db, analysis_id, user)
    try:
        service.review_analysis(db, row, actor_id=user.id, state=payload.state, note=payload.note,
                                expected_revision=payload.expected_revision)
        db.commit()
    except LandCoverError as error:
        db.rollback()
        raise HTTPException(409, str(error)) from error
    return private(service.analysis_json(db, row))


@router.get("/api/land-cover/{analysis_id}/geojson")
def geojson(analysis_id: uuid.UUID, user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    row = find_analysis(db, analysis_id, user)
    record_audit(db, actor_id=user.id, action="land_cover_geojson_viewed", entity_type="land_cover_analysis", entity_id=row.id)
    db.commit()
    return private({**(row.features_json or {"type": "FeatureCollection", "features": []}),
                    "model_version": row.model_version, "review_state": row.review_state, "warning": service.WARNING})


@router.get("/api/land-cover/{analysis_id}/artifacts/{kind}")
def artifact(analysis_id: uuid.UUID, kind: Literal["labels", "preview", "input", "validity"], user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    row = find_analysis(db, analysis_id, user)
    key = row.artifact_keys_json.get(kind)
    if not key:
        raise HTTPException(404, "Artifact not available.")
    record_audit(db, actor_id=user.id, action="land_cover_artifact_viewed", entity_type="land_cover_analysis",
                 entity_id=row.id, after={"kind": kind})
    db.commit()
    try:
        content = create_storage(get_settings()).read(key)
    except (OSError, ValueError) as error:
        raise HTTPException(404, "Artifact is unavailable in private storage.") from error
    suffix, mime = ("png", "image/png") if kind == "preview" else ("tif", "image/tiff")
    return Response(content, media_type=mime, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'{"inline" if kind == "preview" else "attachment"}; filename="land-cover-{kind}-{row.id}.{suffix}"'})


@router.get("/api/land-cover/{analysis_id}/report", response_class=HTMLResponse)
def report(analysis_id: uuid.UUID, user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    row = find_analysis(db, analysis_id, user)
    data = service.analysis_json(db, row)
    esc = lambda v: html.escape(str(v))
    rows = "".join(f'<tr><td>{esc(c["class_name"].replace("_", " "))}</td><td>{c["estimated_area_sqm"]:.0f}</td>'
        f'<td>{c["fraction"]:.1%}</td><td>{esc(round(c["mean_probability"], 3) if c["mean_probability"] is not None else "—")}</td></tr>'
        for c in data["summary"].get("classes", []))
    warnings = "".join(f'<li>{esc(w)}</li>' for w in data["summary"].get("warnings", []))
    body = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Land-cover evidence report</title>
    <style>body{{font:16px system-ui;max-width:900px;margin:40px auto;padding:20px;color:#213b30}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:10px;text-align:left}}img{{max-width:100%;max-height:400px}}@media print{{button{{display:none}}}}</style>
    <h1>Satellite land-cover evidence</h1><p>{esc(service.WARNING)}</p>
    <p>State: {esc(data["state"])} · Review: {esc(row.review_state)} · Boundary: {esc(row.boundary_version)}</p>
    <p>Model: {esc(row.model_version)} · Acquisition: {esc(row.provenance_json.get("acquired_at", "Unavailable"))}</p>
    <p>Scene: {esc(row.provenance_json.get("scene_id", "Unavailable"))}</p>
    <p>Resolution: 10 m. Areas are pixel estimates. Fractions use valid clear pixels; model probability is not measured accuracy.</p>
    <table><thead><tr><th>Land cover</th><th>Estimated m²</th><th>Valid-pixel fraction</th><th>Mean model probability</th></tr></thead><tbody>{rows}</tbody></table>
    <ul>{warnings}</ul><p>Review note: {esc(row.review_note)}</p>
    <p>Weights SHA-256: {esc(row.provenance_json.get("weights_sha256", "Unavailable"))}</p>
    <p>Local ground-truth evaluation: not performed. The research presentation's compact-band scores do not describe this model.</p></html>'''
    record_audit(db, actor_id=user.id, action="land_cover_report_viewed", entity_type="land_cover_analysis", entity_id=row.id)
    db.commit()
    return HTMLResponse(body, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
