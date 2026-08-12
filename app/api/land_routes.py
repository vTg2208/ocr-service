"""Optional land-record enrichment endpoints layered on top of base OCR."""

import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from openai import AsyncOpenAI

from app.api.routes import ocr_endpoint
from app.config import get_settings
from app.models.response_models import (
    LandExtractionResult,
    OCRLandResponse,
)
from app.services.land_enrichment import LandEnrichmentService

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

_land_client = (
    AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    if settings.llm_api_key
    else None
)
_land_service = LandEnrichmentService(
    client=_land_client,
    model_name=settings.llm_model_name,
)


@router.post("/land/extract", response_model=LandExtractionResult)
async def extract_land_endpoint(ocr_text: str = Form(...)) -> LandExtractionResult:
    if not ocr_text.strip():
        raise HTTPException(status_code=400, detail="OCR text must not be empty.")
    return await _land_service.extract(ocr_text)


@router.post("/ocr/land", response_model=OCRLandResponse)
async def ocr_land_endpoint(file: UploadFile = File(...)) -> OCRLandResponse:
    ocr = await ocr_endpoint(file=file, prompt=None)
    try:
        land = await _land_service.extract(ocr.text)
    except Exception:
        logger.exception("Land enrichment failed after successful OCR.")
        land = LandExtractionResult(
            status="failed",
            records=[],
            record_count=0,
            requires_human_review=True,
            warnings=[
                "Land enrichment failed; the independent base OCR result is still available."
            ],
            error="Land enrichment failed.",
        )
    return OCRLandResponse(ocr=ocr, land_extraction=land)
