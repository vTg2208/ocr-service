"""Supporting cadastral extraction endpoints for FRA evidence preparation."""

import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from openai import AsyncOpenAI

from app.api.document_intelligence_routes import ocr_endpoint
from app.config import get_settings
from app.models.response_models import (
    LandExtractionResult,
    OCRLandResponse,
)
from app.services.cadastral_enrichment import CadastralEnrichmentService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Supporting cadastral evidence"])
settings = get_settings()

_land_client = (
    AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    if settings.llm_api_key
    else None
)
_land_service = CadastralEnrichmentService(
    client=_land_client,
    model_name=settings.llm_model_name,
)


@router.post("/land/extract", include_in_schema=False)
@router.post(
    "/api/cadastral-evidence/text/extract",
    response_model=LandExtractionResult,
    summary="Extract supporting land-record fields from reviewed text",
)
async def extract_cadastral_text_endpoint(ocr_text: str = Form(...)) -> LandExtractionResult:
    if not ocr_text.strip():
        raise HTTPException(status_code=400, detail="OCR text must not be empty.")
    return await _land_service.extract(ocr_text)


@router.post("/ocr/land", include_in_schema=False)
@router.post(
    "/api/cadastral-evidence/documents/extract",
    response_model=OCRLandResponse,
    summary="Digitize and extract a supporting land record",
)
async def extract_cadastral_document_endpoint(file: UploadFile = File(...)) -> OCRLandResponse:
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
