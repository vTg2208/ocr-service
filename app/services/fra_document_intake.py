"""Secure multipart ingestion for Tamil Nadu legacy FRA document batches."""

from dataclasses import dataclass
import hashlib
from pathlib import Path

from sqlalchemy import select

from app.db.fra_completion_models import FRAImportBatch, ProcessingJob
from app.db.models import Document
from app.services.fra_archive import (
    ArchiveValidationError,
    create_archive_record,
    create_import_batch,
)
from app.services.malware import MalwareDetectedError, MalwareScannerUnavailable
from app.services.processing_jobs import enqueue_job
from app.utils.file_validation import FileValidationError, validate_upload


@dataclass(frozen=True)
class ArchiveUpload:
    filename: str
    content_type: str | None
    content: bytes


def _media_type(extension: str) -> str:
    return {
        "pdf": "application/pdf",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "bmp": "image/bmp",
        "tif": "image/tiff",
        "tiff": "image/tiff",
    }.get(extension, "application/octet-stream")


def _file_error(filename: str, code: str, message: str) -> dict:
    return {
        "filename": filename or "upload",
        "status": "rejected",
        "error_code": code,
        "message": message,
    }


def _accepted_file(record, job) -> dict:
    return {
        "filename": record.document.original_filename,
        "legacy_reference": record.legacy_reference,
        "status": "accepted",
        "record_id": str(record.id),
        "document_id": str(record.document_id),
        "processing_job_id": str(job.id) if job is not None else None,
    }


def _existing_result(session, batch: FRAImportBatch) -> dict:
    files = []
    for record in sorted(batch.records, key=lambda item: (item.created_at, str(item.id))):
        job = session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.task_type == "archive_extract",
                ProcessingJob.entity_id == record.id,
            )
        )
        files.append(_accepted_file(record, job))
    files.extend(list((batch.error_summary_json or {}).get("files", [])))
    return {
        "batch_id": str(batch.id),
        "batch_status": batch.status,
        "accepted": len(batch.records),
        "rejected": batch.failed_count,
        "replayed": True,
        "files": files,
    }


def ingest_archive_batch(
    session,
    *,
    files: list[ArchiveUpload],
    source_office: str,
    district: str,
    actor_id,
    idempotency_key: str,
    storage,
    scanner,
    state: str = "Tamil Nadu",
    synthetic: bool = False,
    request_id: str | None = None,
    enqueue=enqueue_job,
) -> dict:
    """Validate and persist a batch while isolating failure to each file."""

    source = " ".join(source_office.split())
    district_name = " ".join(district.split())
    key = " ".join(idempotency_key.split())
    if not source or not district_name or not key:
        raise ArchiveValidationError(
            "Source office, district, and Idempotency-Key are required."
        )
    if not files:
        raise ArchiveValidationError("At least one archive file is required.")

    existing = session.scalar(
        select(FRAImportBatch).where(
            FRAImportBatch.created_by == actor_id,
            FRAImportBatch.idempotency_key == key,
        )
    )
    if existing is not None:
        return _existing_result(session, existing)

    batch = create_import_batch(
        session,
        source_label=source,
        state=state,
        actor_id=actor_id,
        idempotency_key=key,
        synthetic=synthetic,
        provenance={
            "source": source,
            "source_office": source,
            "district": district_name,
            "synthetic": synthetic,
            "ingest_method": "multipart_batch_upload",
        },
        request_id=request_id,
    )
    results = []
    seen_checksums = set()
    seen_references = set()
    accepted = 0

    for position, upload in enumerate(files, start=1):
        storage_key = None
        try:
            validated = validate_upload(upload.filename, upload.content)
            scanner.scan(upload.content)
            checksum = hashlib.sha256(upload.content).hexdigest()
            duplicate = checksum in seen_checksums or session.scalar(
                select(Document.id).where(
                    Document.uploaded_by == actor_id,
                    Document.sha256 == checksum,
                ).limit(1)
            )
            if duplicate:
                raise ArchiveValidationError(
                    "This document has already been submitted by the current uploader."
                )
            legacy_reference = Path(validated.safe_filename).stem.strip("._-")
            if not legacy_reference:
                legacy_reference = f"archive-record-{position}"
            normalized_reference = legacy_reference.casefold()
            if normalized_reference in seen_references:
                raise ArchiveValidationError(
                    "Another file in this batch has the same legacy reference."
                )

            storage_key = storage.put(upload.content, f".{validated.extension}")
            try:
                with session.begin_nested():
                    document = Document(
                        uploaded_by=actor_id,
                        storage_key=storage_key,
                        original_filename=validated.safe_filename,
                        content_type=_media_type(validated.extension),
                        sha256=checksum,
                        ocr_status="queued",
                        idempotency_key=f"fra-archive:{batch.id}:{position}:{checksum[:16]}",
                    )
                    session.add(document)
                    session.flush()
                    record = create_archive_record(
                        session,
                        batch=batch,
                        document_id=document.id,
                        legacy_reference=legacy_reference,
                        actor_id=actor_id,
                        synthetic=synthetic,
                        provenance={
                            **dict(batch.provenance_json),
                            "filename": validated.safe_filename,
                            "sha256": checksum,
                        },
                        request_id=request_id,
                    )
                    job = enqueue(
                        session,
                        task_type="archive_extract",
                        entity_type="archive_record",
                        entity_id=record.id,
                        actor_id=actor_id,
                        idempotency_key=f"archive-extract:{checksum}",
                        payload={"record_id": str(record.id), "manifest": {}},
                    )
                    session.flush()
            except Exception:
                storage.delete(storage_key)
                storage_key = None
                raise
            seen_checksums.add(checksum)
            seen_references.add(normalized_reference)
            accepted += 1
            results.append(_accepted_file(record, job))
        except FileValidationError as error:
            results.append(_file_error(upload.filename, "invalid_file", error.message))
        except MalwareDetectedError as error:
            results.append(_file_error(upload.filename, "malware_detected", str(error)))
        except MalwareScannerUnavailable as error:
            results.append(_file_error(upload.filename, "scanner_unavailable", str(error)))
        except ArchiveValidationError as error:
            code = "duplicate_file" if "already been submitted" in str(error) else "invalid_metadata"
            results.append(_file_error(upload.filename, code, str(error)))
        except Exception:
            if storage_key is not None:
                storage.delete(storage_key)
            results.append(_file_error(
                upload.filename,
                "processing_setup_failed",
                "The document could not be queued for processing.",
            ))

    rejected_files = [item for item in results if item["status"] == "rejected"]
    batch.failed_count = len(rejected_files)
    batch.error_summary_json = {"files": rejected_files}
    if accepted and rejected_files:
        batch.status = "partial"
    elif accepted:
        batch.status = "processing"
    else:
        batch.status = "failed"
    session.flush()
    return {
        "batch_id": str(batch.id),
        "batch_status": batch.status,
        "accepted": accepted,
        "rejected": len(rejected_files),
        "replayed": False,
        "files": results,
    }
