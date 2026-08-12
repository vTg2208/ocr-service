"""
Application entrypoint.

Creates the FastAPI app, wires up routing, logging, and a global
exception handler so every error response — expected or not — follows
the same {"success": false, "message": "..."} shape.
"""

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.api.land_routes import router as land_router
from app.config import get_settings

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    description="Standalone OCR microservice for extracting text from images and PDFs.",
    version="1.0.0",
)

app.include_router(router)
app.include_router(land_router)


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


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("%s starting up.", settings.app_name)
