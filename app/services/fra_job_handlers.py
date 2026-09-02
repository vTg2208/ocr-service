"""Registry for FRA background job handlers."""

from collections.abc import Callable
from functools import lru_cache
import time
from typing import Any
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.db.fra_completion_models import FRAArchiveRecord, ModelVersion
from app.services.processing_jobs import JobExecutionError


JobHandler = Callable[[Any, Any], dict]
JOB_HANDLERS: dict[str, JobHandler] = {}


def register_job_handler(task_type: str, handler: JobHandler) -> None:
    normalized = task_type.strip()
    if not normalized:
        raise ValueError("A task type is required.")
    JOB_HANDLERS[normalized] = handler


def get_job_handler(task_type: str) -> JobHandler | None:
    return JOB_HANDLERS.get(task_type)


@lru_cache(maxsize=1)
def _ocr_engine():
    from app.services.ocr_engine import PaddleOCREngine

    return PaddleOCREngine()


def _read_archive_document(record: FRAArchiveRecord) -> bytes:
    from app.services.storage import create_storage

    return create_storage(get_settings()).read(record.document.storage_key)


def _recognize_archive_document(content: bytes, filename: str):
    from app.services.image_processor import ImageProcessor
    from app.services.pdf_processor import PDFProcessor
    from app.utils.file_validation import validate_upload

    started = time.perf_counter()
    validated = validate_upload(filename, content)
    engine = _ocr_engine()
    if validated.is_pdf:
        raw_text, confidence_percent = PDFProcessor(engine).process(validated.content)
    else:
        image = ImageProcessor.decode(validated.content)
        raw_text, confidence_percent = engine.extract_text(image)
    settings = get_settings()
    model_version = (
        f"{settings.paddleocr_detection_model_name}+"
        f"{settings.paddleocr_recognition_model_name}"
    )
    elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
    return raw_text, float(confidence_percent) / 100, model_version, elapsed_ms


def _active_entity_model(session, payload: dict) -> ModelVersion:
    identifier = payload.get("model_version_id")
    if identifier:
        try:
            model = session.get(
                ModelVersion,
                uuid.UUID(identifier) if isinstance(identifier, str) else identifier,
            )
        except (TypeError, ValueError) as error:
            raise JobExecutionError(
                "model_configuration_invalid",
                "The requested FRA entity model identifier is invalid.",
                retriable=False,
            ) from error
        if model is not None and model.status == "active" and model.task in {
            "entity_extraction", "fra_entity_extraction", "archive_extraction",
        }:
            return model
    else:
        model = session.scalar(
            select(ModelVersion)
            .where(
                ModelVersion.task.in_((
                    "entity_extraction", "fra_entity_extraction", "archive_extraction",
                )),
                ModelVersion.status == "active",
            )
            .order_by(ModelVersion.activated_at.desc(), ModelVersion.registered_at.desc())
            .limit(1)
        )
        if model is not None:
            return model
    raise JobExecutionError(
        "model_unavailable",
        "No active FRA entity model is attached.",
        retriable=True,
    )


def _archive_extract(session, job):
    from app.services.fra_archive import process_archive_extraction
    from app.services.fra_adapter_factory import create_entity_extractor
    from app.services.model_gateway import (
        ManifestFRAEntityExtractor,
        ModelRegistrationError,
    )

    record = session.get(FRAArchiveRecord, job.entity_id)
    if record is None:
        raise ValueError("Archive record does not exist.")
    payload = dict(job.payload_json or {})
    if record.synthetic:
        extractor = ManifestFRAEntityExtractor(
            payload.get("model_version") or "tn-manifest-v1"
        )
        manifest = payload.get("manifest") or {}
        raw_text = payload.get("raw_text") or ""
        ocr_model_version = None
        entity_model_version_id = None
    else:
        model = _active_entity_model(session, payload)
        try:
            extractor = create_entity_extractor(model)
        except ModelRegistrationError as error:
            raise JobExecutionError(
                "model_configuration_invalid", str(error), retriable=False
            ) from error
        content = _read_archive_document(record)
        raw_text, ocr_confidence, ocr_model_version, _ocr_time_ms = (
            _recognize_archive_document(content, record.document.original_filename)
        )
        manifest = {
            "raw_text": raw_text,
            "ocr_confidence": ocr_confidence,
            "state_code": record.state_code,
        }
        entity_model_version_id = model.id
    run = process_archive_extraction(
        session,
        record,
        extractor=extractor,
        manifest=manifest,
        raw_text=raw_text,
        ocr_model_version=ocr_model_version,
        entity_model_version_id=entity_model_version_id,
        actor_id=job.requested_by,
    )
    record.document.ocr_status = "completed"
    return {"extraction_run_id": str(run.id)}


def _asset_inference(session, job):
    from app.db.fra_completion_models import ModelVersion
    from app.services.fra_assets import process_asset_inference
    from app.services.model_gateway import ManifestAssetDetector

    model_identifier = (job.payload_json or {}).get("model_version_id")
    model = session.get(
        ModelVersion,
        uuid.UUID(model_identifier) if isinstance(model_identifier, str) else model_identifier,
    )
    if model is None:
        raise ValueError("Model version does not exist.")
    assets = process_asset_inference(
        session, job, adapter=ManifestAssetDetector(model.version)
    )
    return {"asset_ids": [str(asset.id) for asset in assets]}


register_job_handler("archive_extract", _archive_extract)
register_job_handler("asset_inference", _asset_inference)
