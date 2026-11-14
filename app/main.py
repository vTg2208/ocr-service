"""
Application entrypoint.

Creates the FastAPI app, wires up routing, logging, and a global
exception handler so every error response — expected or not — follows
the same {"success": false, "message": "..."} shape.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.document_intelligence_routes import router as document_intelligence_router
from app.api.cadastral_enrichment_routes import router as cadastral_enrichment_router
from app.api.cadastral_evidence_routes import router as cadastral_evidence_router
from app.api.session_routes import router as session_router
from app.api.cadastral_registry_routes import router as cadastral_registry_router
from app.api.fra_routes import router as fra_router
from app.api.fra_archive_routes import router as fra_archive_router
from app.api.fra_operations_routes import router as fra_operations_router
from app.api.fra_atlas_routes import router as fra_atlas_router
from app.api.fra_asset_routes import router as fra_asset_router
from app.api.fra_planning_routes import router as fra_planning_router
from app.api.fra_intake_routes import router as fra_intake_router
from app.api.fra_case_routes import router as fra_case_router
from app.api.fra_geospatial_routes import router as fra_geospatial_router
from app.api.fra_evidence_routes import router as fra_evidence_router
from app.api.fra_dashboard_routes import router as fra_dashboard_router
from app.api.user_portal_routes import router as user_portal_router
from app.api.community_routes import router as community_router
from app.api.land_cover_routes import router as land_cover_router
from app.api.oidc_routes import router as oidc_router
from app.api.document_evidence_routes import router as document_evidence_router
from starlette.middleware.sessions import SessionMiddleware
from app.api.auth import SESSION_COOKIE, get_current_user
from app.config import get_settings
from app.db.session import get_db
from app.middleware import RequestContextMiddleware
from sqlalchemy.orm import Session

settings = get_settings()


class RevalidatingStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("%s starting up.", settings.app_name)
    yield
    logger.info("%s shutting down.", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    description=(
        "AranyaSetu is an FRA Spatial Intelligence and Decision Support platform for "
        "digitizing records, managing rights workflows, mapping evidence, and producing "
        "reviewable scheme recommendations."
    ),
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(SessionMiddleware, secret_key=settings.auth_secret, session_cookie="aranyasetu_oidc",
    max_age=600, same_site="lax", https_only=settings.environment == "production")
app.add_middleware(
    RequestContextMiddleware, requests=settings.rate_limit_requests,
    window_seconds=settings.rate_limit_window_seconds,
    redis_url=settings.redis_url,
)

app.include_router(document_intelligence_router)
app.include_router(cadastral_enrichment_router)
app.include_router(cadastral_evidence_router)
app.include_router(session_router)
app.include_router(oidc_router)
app.include_router(document_evidence_router)
app.include_router(user_portal_router)
app.include_router(community_router)
app.include_router(land_cover_router)
app.include_router(cadastral_registry_router)
app.include_router(fra_router)
app.include_router(fra_archive_router)
app.include_router(fra_operations_router)
app.include_router(fra_atlas_router)
app.include_router(fra_asset_router)
app.include_router(fra_planning_router)
app.include_router(fra_intake_router)
app.include_router(fra_case_router)
app.include_router(fra_geospatial_router)
app.include_router(fra_evidence_router)
app.include_router(fra_dashboard_router)

static_root = Path(__file__).parent / "static"
app.mount("/static", RevalidatingStaticFiles(directory=static_root), name="static")


@app.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    return RedirectResponse("/login")


@app.get("/login", include_in_schema=False)
async def login_ui() -> FileResponse:
    return FileResponse(static_root / "login" / "index.html")


@app.get("/cadastral-evidence", include_in_schema=False)
async def cadastral_evidence_ui() -> FileResponse:
    return FileResponse(static_root / "cadastral-evidence" / "index.html")


@app.get("/land-mapping", include_in_schema=False)
async def legacy_cadastral_workspace_redirect() -> RedirectResponse:
    """Keep bookmarks working while directing staff to the renamed workspace."""
    return RedirectResponse("/cadastral-evidence", status_code=308)


@app.get("/fra", include_in_schema=False)
async def fra_workspace(request: Request, db: Session = Depends(get_db)):
    try:
        signed_in = get_current_user(
            authorization=request.headers.get("Authorization"),
            session_token=request.cookies.get(SESSION_COOKIE),
            db=db,
        )
    except HTTPException:
        return RedirectResponse("/login")
    if signed_in.role == "user":
        return RedirectResponse("/my-plots")
    return FileResponse(static_root / "fra" / "index.html")


@app.get("/my-plots", include_in_schema=False)
async def user_workspace(request: Request, db: Session = Depends(get_db)):
    try:
        get_current_user(authorization=request.headers.get("Authorization"), session_token=request.cookies.get(SESSION_COOKIE), db=db)
    except HTTPException:
        return RedirectResponse("/login")
    return FileResponse(static_root / "user" / "index.html")


@app.get("/staff-services", include_in_schema=False)
async def staff_services(request: Request, db: Session = Depends(get_db)):
    try:
        user = get_current_user(authorization=request.headers.get("Authorization"), session_token=request.cookies.get(SESSION_COOKIE), db=db)
    except HTTPException:
        return RedirectResponse("/login")
    if user.role not in {"reviewer", "admin"}:
        return RedirectResponse("/my-plots")
    return FileResponse(static_root / "staff-services" / "index.html")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.detail},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"success": False, "message": "Internal server error."},
    )
