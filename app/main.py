"""
Application entrypoint.

Creates the FastAPI app, wires up routing, logging, and a global
exception handler so every error response — expected or not — follows
the same {"success": false, "message": "..."} shape.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.api.land_routes import router as land_router
from app.api.patta_routes import router as patta_router
from app.api.session_routes import router as session_router
from app.api.claim_registry_routes import router as claim_registry_router
from app.api.fra_routes import router as fra_router
from app.config import get_settings
from app.middleware import RequestContextMiddleware

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
    description="Standalone OCR microservice for extracting text from images and PDFs.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    RequestContextMiddleware, requests=settings.rate_limit_requests,
    window_seconds=settings.rate_limit_window_seconds,
)

app.include_router(router)
app.include_router(land_router)
app.include_router(patta_router)
app.include_router(session_router)
app.include_router(claim_registry_router)
app.include_router(fra_router)

static_root = Path(__file__).parent / "static"
app.mount("/static", RevalidatingStaticFiles(directory=static_root), name="static")


@app.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    return RedirectResponse("/login")


@app.get("/login", include_in_schema=False)
async def login_ui() -> FileResponse:
    return FileResponse(static_root / "login" / "index.html")


@app.get("/land-mapping", include_in_schema=False)
async def land_mapping_ui() -> FileResponse:
    return FileResponse(static_root / "land-mapping" / "index.html")


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
