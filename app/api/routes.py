"""
API route definitions.

Two endpoints: a health check and the core OCR endpoint. All heavy
lifting (validation, preprocessing, OCR) is delegated to the services
layer — this module only orchestrates the request/response cycle.
"""

import logging
import time
import threading
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.models.response_models import HealthResponse, OCREvaluationResponse, OCRResponse
from app.services.evaluation import evaluate_ocr_text
from app.services.image_processor import ImageProcessor
from app.services.ocr_engine import OCRException, OCRInitializationError, PaddleOCREngine
from app.services.pdf_processor import PDFProcessingError, PDFProcessor
from app.services.llm_service import LLMService
from app.services.quality_assessment import assess_ocr_quality
from app.utils.file_validation import FileValidationError, validate_upload

logger = logging.getLogger(__name__)
router = APIRouter()

_image_processor = ImageProcessor()
_llm_service = LLMService()
_ocr_engine: Optional[PaddleOCREngine] = None
_pdf_processor: Optional[PDFProcessor] = None
_engine_lock = threading.RLock()


def _get_ocr_engine() -> PaddleOCREngine:
    global _ocr_engine
    if _ocr_engine is None:
        with _engine_lock:
            if _ocr_engine is None:
                _ocr_engine = PaddleOCREngine()
    return _ocr_engine


def _get_pdf_processor() -> PDFProcessor:
    global _pdf_processor
    if _pdf_processor is None:
        with _engine_lock:
            if _pdf_processor is None:
                _pdf_processor = PDFProcessor(
                    ocr_engine=_get_or_raise_ocr_engine(),
                    image_processor=_image_processor,
                )
    return _pdf_processor


def _get_or_raise_ocr_engine() -> PaddleOCREngine:
    try:
        return _get_ocr_engine()
    except OCRInitializationError as exc:
        raise HTTPException(
            status_code=503,
            detail="OCR service is unavailable because the OCR engine could not be initialized.",
        ) from exc


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(status="healthy")


@router.post("/evaluate", response_model=OCREvaluationResponse)
async def evaluate_endpoint(
    reference_text: str = Form(...),
    ocr_text: str = Form(...),
) -> OCREvaluationResponse:
    try:
        return evaluate_ocr_text(reference_text, ocr_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/ocr",
    response_model=OCRResponse,
    responses={400: {"description": "Validation error"}, 500: {"description": "OCR failure"}},
)
async def ocr_endpoint(file: UploadFile = File(...), prompt: str | None = Form(None)) -> OCRResponse:
    start_time = time.perf_counter()
    content = await file.read()

    try:
        validated = validate_upload(file.filename, content)
    except FileValidationError as exc:
        logger.warning("Rejected upload '%s': %s", file.filename, exc.message)
        raise HTTPException(status_code=400, detail=exc.message) from exc

    logger.info(
        "Processing upload: filename=%s type=%s size_bytes=%d",
        validated.safe_filename,
        validated.extension,
        len(content),
    )

    try:
        if validated.is_pdf:
            text, confidence = _get_pdf_processor().process(validated.content)
        else:
            image = ImageProcessor.decode(validated.content)
            text, confidence = _get_or_raise_ocr_engine().extract_text(image)
    except PDFProcessingError as exc:
        logger.warning("PDF processing failed for '%s': %s", validated.safe_filename, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OCRException as exc:
        logger.error("OCR failed for '%s': %s", validated.safe_filename, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        # e.g. image bytes could not be decoded despite passing validation
        logger.error("Decoding failed for '%s': %s", validated.safe_filename, exc)
        raise HTTPException(status_code=400, detail="Uploaded file could not be processed.") from exc
    except Exception as exc:  # noqa: BLE001 - final safety net
        logger.error("Unexpected failure for '%s': %s", validated.safe_filename, exc)
        raise HTTPException(status_code=500, detail="OCR processing failed.") from exc

    processing_time = round(time.perf_counter() - start_time, 2)

    logger.info(
        "Completed OCR: filename=%s processing_time=%.2fs confidence=%.2f",
        validated.safe_filename,
        processing_time,
        confidence,
    )

    analysis = None
    if prompt:
        logger.info("Analyzing OCR text with LLM based on user prompt")
        analysis = await _llm_service.analyze_text(text, prompt)

    quality = assess_ocr_quality(text, confidence)

    return OCRResponse(
        success=True,
        filename=validated.safe_filename,
        processing_time=processing_time,
        text=text,
        confidence=confidence,
        quality=quality,
        prompt_provided=bool(prompt),
        analysis=analysis,
    )
