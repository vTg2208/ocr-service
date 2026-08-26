"""Registry for FRA background job handlers."""

from collections.abc import Callable
from typing import Any
import uuid


JobHandler = Callable[[Any, Any], dict]
JOB_HANDLERS: dict[str, JobHandler] = {}


def register_job_handler(task_type: str, handler: JobHandler) -> None:
    normalized = task_type.strip()
    if not normalized:
        raise ValueError("A task type is required.")
    JOB_HANDLERS[normalized] = handler


def get_job_handler(task_type: str) -> JobHandler | None:
    return JOB_HANDLERS.get(task_type)


def _archive_extract(session, job):
    from app.db.fra_completion_models import FRAArchiveRecord
    from app.services.fra_archive import process_archive_extraction
    from app.services.model_gateway import ManifestFRAEntityExtractor

    record = session.get(FRAArchiveRecord, job.entity_id)
    if record is None:
        raise ValueError("Archive record does not exist.")
    payload = dict(job.payload_json or {})
    run = process_archive_extraction(
        session,
        record,
        extractor=ManifestFRAEntityExtractor(payload.get("model_version") or "tn-manifest-v1"),
        manifest=payload.get("manifest") or {},
        raw_text=payload.get("raw_text") or "",
        actor_id=job.requested_by,
    )
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
