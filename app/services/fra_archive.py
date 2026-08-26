"""Tamil Nadu FRA archive intake, review, search, and native promotion."""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import String, and_, cast, func, or_, select

from app.db.fra_completion_models import FRAArchiveRecord, FRAExtractionRun, FRAImportBatch
from app.db.fra_models import FRAClaim, GramSabha, RightsHolder
from app.db.models import Document, User
from app.services.audit import record_audit
from app.services.fra_claims import RIGHT_TYPES, create_claim
from app.services.state_profiles import get_state_profile


REQUIRED_REVIEW_FIELDS = {
    "holder_name",
    "district",
    "block",
    "village",
    "right_type",
    "claim_status",
}
REVIEW_ROLES = {"reviewer", "admin"}


class ArchiveValidationError(ValueError):
    pass


class ArchiveConflictError(RuntimeError):
    pass


def _require_user(session, user_id, *, reviewer: bool = False) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise ArchiveValidationError("The archive actor does not exist.")
    if reviewer and user.role not in REVIEW_ROLES:
        raise PermissionError("Archive review and promotion require a reviewer or admin.")
    return user


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def create_import_batch(
    session,
    *,
    source_label: str,
    state: str,
    actor_id,
    idempotency_key: str,
    synthetic: bool,
    provenance: dict,
    request_id: str | None = None,
) -> FRAImportBatch:
    _require_user(session, actor_id)
    profile = get_state_profile(state)
    label = _clean(source_label)
    key = _clean(idempotency_key)
    if not label or not key:
        raise ArchiveValidationError("Source label and idempotency key are required.")
    if not isinstance(provenance, dict) or not _clean(provenance.get("source")):
        raise ArchiveValidationError("Archive source provenance is required.")
    if bool(provenance.get("synthetic", False)) != bool(synthetic):
        raise ArchiveValidationError("Batch provenance and synthetic flag must agree.")
    existing = session.scalar(
        select(FRAImportBatch).where(
            FRAImportBatch.created_by == actor_id,
            FRAImportBatch.idempotency_key == key,
        )
    )
    if existing is not None:
        return existing
    batch = FRAImportBatch(
        source_label=label,
        state_code=profile.code,
        created_by=actor_id,
        idempotency_key=key,
        status="pending",
        provenance_json=dict(provenance),
        synthetic=synthetic,
    )
    session.add(batch)
    session.flush()
    record_audit(
        session,
        actor_id=actor_id,
        action="fra_archive_batch_created",
        entity_type="fra_import_batch",
        entity_id=batch.id,
        after={"state_code": profile.code, "synthetic": synthetic},
        request_id=request_id,
    )
    return batch


def create_archive_record(
    session,
    *,
    batch: FRAImportBatch,
    document_id,
    legacy_reference: str,
    actor_id,
    synthetic: bool | None = None,
    provenance: dict | None = None,
    request_id: str | None = None,
) -> FRAArchiveRecord:
    _require_user(session, actor_id)
    get_state_profile(batch.state_code)
    reference = _clean(legacy_reference)
    if not reference:
        raise ArchiveValidationError("A legacy reference is required.")
    record_synthetic = batch.synthetic if synthetic is None else synthetic
    if bool(record_synthetic) != bool(batch.synthetic):
        raise ArchiveValidationError("Record and batch synthetic flag must agree.")
    document = session.get(Document, document_id)
    if document is None:
        raise ArchiveValidationError("Archive document does not exist.")
    if document.storage_key.casefold().startswith(("http://", "https://")):
        raise ArchiveValidationError("Archive documents must use private storage.")
    existing = session.scalar(
        select(FRAArchiveRecord).where(
            FRAArchiveRecord.batch_id == batch.id,
            FRAArchiveRecord.legacy_reference == reference,
        )
    )
    if existing is not None:
        if existing.document_id != document.id:
            raise ArchiveConflictError("The legacy reference already uses another document.")
        return existing
    fingerprint_source = "|".join(
        [batch.state_code.casefold(), reference.casefold(), document.sha256.casefold()]
    )
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
    record = FRAArchiveRecord(
        batch=batch,
        document=document,
        legacy_reference=reference,
        state_code=batch.state_code,
        review_state="pending",
        duplicate_fingerprint=fingerprint,
        provenance_json=dict(provenance or batch.provenance_json),
        synthetic=record_synthetic,
    )
    batch.record_count += 1
    batch.status = "processing"
    session.add(record)
    session.flush()
    record_audit(
        session,
        actor_id=actor_id,
        action="fra_archive_record_created",
        entity_type="fra_archive_record",
        entity_id=record.id,
        after={
            "batch_id": str(batch.id),
            "legacy_reference": reference,
            "synthetic": record_synthetic,
        },
        request_id=request_id,
    )
    return record


def process_archive_extraction(
    session,
    record: FRAArchiveRecord,
    *,
    extractor,
    manifest: dict,
    actor_id,
    raw_text: str = "",
    ocr_model_version: str | None = None,
    processing_time_ms: int | None = None,
    request_id: str | None = None,
) -> FRAExtractionRun:
    _require_user(session, actor_id)
    if record.state_code != "TN":
        get_state_profile(record.state_code)
    result = extractor.extract(record.legacy_reference, manifest)
    first_run = not record.extraction_runs
    run = FRAExtractionRun(
        archive_record=record,
        ocr_model_version=ocr_model_version,
        entity_model_version=result.model_version,
        raw_text=str(raw_text),
        standardized_json=dict(result.fields),
        field_evidence_json=dict(result.field_evidence),
        provenance_json=dict(result.provenance),
        overall_confidence=result.confidence,
        processing_time_ms=(
            processing_time_ms if processing_time_ms is not None else result.processing_time_ms
        ),
    )
    record.review_state = "needs_review"
    if first_run:
        record.batch.processed_count += 1
    if record.batch.processed_count >= record.batch.record_count:
        record.batch.status = "needs_review"
    session.add(run)
    session.flush()
    record_audit(
        session,
        actor_id=actor_id,
        action="fra_archive_extraction_created",
        entity_type="fra_archive_record",
        entity_id=record.id,
        after={
            "extraction_run_id": str(run.id),
            "entity_model_version": result.model_version,
            "legal_role": "unverified_extraction",
        },
        request_id=request_id,
    )
    return run


def _validated_review_fields(record: FRAArchiveRecord, values: dict) -> dict:
    if not isinstance(values, dict):
        raise ArchiveValidationError("Reviewed fields must be an object.")
    missing = sorted(field for field in REQUIRED_REVIEW_FIELDS if not _clean(values.get(field)))
    if missing:
        raise ArchiveValidationError(f"Required reviewed fields are missing: {', '.join(missing)}.")
    profile = get_state_profile(record.state_code)
    reviewed = dict(values)
    reviewed["holder_name"] = _clean(values["holder_name"])
    reviewed["district"] = profile.normalize_district(str(values["district"]))
    reviewed["block"] = profile.normalize_block(str(values["block"]))
    reviewed["village"] = profile.normalize_village(str(values["village"]))
    reviewed["right_type"] = _clean(values["right_type"]).upper()
    reviewed["claim_status"] = _clean(values["claim_status"]).casefold()
    if reviewed["right_type"] not in RIGHT_TYPES:
        raise ArchiveValidationError("Right type must be IFR, CR, or CFR.")
    if values.get("claim_number") is not None:
        reviewed["claim_number"] = _clean(values["claim_number"])
    if values.get("claim_year") not in (None, ""):
        try:
            year = int(values["claim_year"])
        except (TypeError, ValueError) as error:
            raise ArchiveValidationError("Claim year must be a four-digit year.") from error
        if year < 1900 or year > datetime.now(timezone.utc).year:
            raise ArchiveValidationError("Claim year must be a four-digit year.")
        reviewed["claim_year"] = year
    return reviewed


def review_archive_record(
    session,
    record: FRAArchiveRecord,
    *,
    reviewed_fields: dict,
    reviewer_id,
    expected_revision: int,
    request_id: str | None = None,
) -> FRAArchiveRecord:
    _require_user(session, reviewer_id, reviewer=True)
    if record.review_state not in {"needs_review", "reviewed"}:
        raise ArchiveConflictError("The archive record is not ready for review.")
    if record.revision != expected_revision:
        raise ArchiveConflictError("The archive record changed since it was loaded.")
    reviewed = _validated_review_fields(record, reviewed_fields)
    before = {"review_state": record.review_state, "revision": record.revision}
    record.reviewed_fields_json = reviewed
    record.claim_number = reviewed.get("claim_number") or record.legacy_reference
    record.holder_display_name = reviewed["holder_name"]
    record.district = reviewed["district"]
    record.block = reviewed["block"]
    record.village = reviewed["village"]
    record.right_type = reviewed["right_type"]
    record.claim_status = reviewed["claim_status"]
    record.claim_year = reviewed.get("claim_year")
    record.review_state = "reviewed"
    record.reviewed_by = reviewer_id
    record.reviewed_at = datetime.now(timezone.utc)
    record.revision += 1
    session.flush()
    record_audit(
        session,
        actor_id=reviewer_id,
        action="fra_archive_record_reviewed",
        entity_type="fra_archive_record",
        entity_id=record.id,
        before=before,
        after={"review_state": "reviewed", "revision": record.revision},
        request_id=request_id,
    )
    return record


def _escaped_like(term: str) -> str:
    escaped = term.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_archive(
    session,
    *,
    query: str = "",
    filters: dict | None = None,
    offset: int = 0,
    limit: int = 50,
) -> list[FRAArchiveRecord]:
    if offset < 0 or not 1 <= limit <= 200:
        raise ArchiveValidationError("Archive pagination is invalid.")
    statement = select(FRAArchiveRecord)
    searchable = [
        FRAArchiveRecord.legacy_reference,
        FRAArchiveRecord.claim_number,
        FRAArchiveRecord.holder_display_name,
        FRAArchiveRecord.district,
        FRAArchiveRecord.block,
        FRAArchiveRecord.village,
        FRAArchiveRecord.right_type,
        FRAArchiveRecord.claim_status,
        cast(FRAArchiveRecord.claim_year, String),
    ]
    terms = [term for term in _clean(query).split(" ") if term]
    for term in terms:
        pattern = _escaped_like(term)
        statement = statement.where(
            or_(*(func.lower(column).like(pattern, escape="\\") for column in searchable))
        )
    filter_columns = {
        "state_code": FRAArchiveRecord.state_code,
        "district": FRAArchiveRecord.district,
        "block": FRAArchiveRecord.block,
        "village": FRAArchiveRecord.village,
        "right_type": FRAArchiveRecord.right_type,
        "claim_status": FRAArchiveRecord.claim_status,
        "review_state": FRAArchiveRecord.review_state,
    }
    for key, value in (filters or {}).items():
        if value in (None, ""):
            continue
        if key == "claim_year":
            statement = statement.where(FRAArchiveRecord.claim_year == int(value))
        elif key in filter_columns:
            statement = statement.where(
                func.lower(filter_columns[key]) == _clean(value).casefold()
            )
        else:
            raise ArchiveValidationError(f"Unsupported archive filter: {key}.")
    return list(
        session.scalars(
            statement.order_by(
                FRAArchiveRecord.created_at.desc(), FRAArchiveRecord.legacy_reference,
                FRAArchiveRecord.id,
            ).offset(offset).limit(limit)
        )
    )


def _gram_sabha_for_record(session, record: FRAArchiveRecord) -> GramSabha:
    reference = "|".join(
        [record.state_code, record.district or "", record.block or "", record.village or ""]
    ).casefold()
    external_reference = f"archive-gram-sabha:{hashlib.sha256(reference.encode()).hexdigest()}"
    existing = session.scalar(
        select(GramSabha).where(GramSabha.external_reference == external_reference)
    )
    if existing is not None:
        return existing
    gram_sabha = GramSabha(
        name=f"{record.village} Gram Sabha",
        village=record.village,
        block=record.block,
        district=record.district,
        state="Tamil Nadu",
        external_reference=external_reference,
        metadata_json={"source": "archive_promotion", "synthetic": record.synthetic},
    )
    session.add(gram_sabha)
    session.flush()
    return gram_sabha


def promote_archive_record(
    session,
    record: FRAArchiveRecord,
    *,
    actor_id,
    request_id: str | None = None,
) -> FRAClaim:
    _require_user(session, actor_id, reviewer=True)
    if record.promoted_claim_id is not None:
        existing = session.get(FRAClaim, record.promoted_claim_id)
        if existing is None:
            raise ArchiveConflictError("The promoted FRA claim no longer exists.")
        return existing
    if record.review_state != "reviewed":
        raise ArchiveConflictError("Only a reviewed archive record can be promoted.")
    fields = _validated_review_fields(record, record.reviewed_fields_json)
    gram_sabha = _gram_sabha_for_record(session, record)
    holder_type = "individual" if fields["right_type"] == "IFR" else "community"
    holder_reference = f"archive-record:{record.id}:rights-holder"
    holder = session.scalar(
        select(RightsHolder).where(RightsHolder.external_reference == holder_reference)
    )
    if holder is None:
        holder = RightsHolder(
            display_name=fields["holder_name"],
            holder_type=holder_type,
            claimant_category=fields.get("claimant_category"),
            external_reference=holder_reference,
            gram_sabha=gram_sabha,
            metadata_json={"source": "archive_promotion", "synthetic": record.synthetic},
        )
        session.add(holder)
        session.flush()
    claim = create_claim(
        session,
        claim_number=fields.get("claim_number") or record.legacy_reference,
        right_type=fields["right_type"],
        rights_holder_id=holder.id,
        submitted_by=actor_id,
        gram_sabha_id=gram_sabha.id if fields["right_type"] in {"CR", "CFR"} else None,
        document_id=record.document_id,
        claimed_area_sqm=fields.get("claimed_area_sqm"),
        provenance={
            "source": "fra_archive_promotion",
            "archive_record_id": str(record.id),
            "legacy_reference": record.legacy_reference,
            "source_claim_status": fields["claim_status"],
            "synthetic": record.synthetic,
        },
        request_id=request_id,
    )
    record.promoted_claim = claim
    record.review_state = "promoted"
    session.flush()
    record_audit(
        session,
        actor_id=actor_id,
        action="fra_archive_record_promoted",
        entity_type="fra_archive_record",
        entity_id=record.id,
        after={"promoted_claim_id": str(claim.id)},
        request_id=request_id,
    )
    return claim
