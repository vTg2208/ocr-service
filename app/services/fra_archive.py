"""Tamil Nadu FRA archive intake, review, search, and native promotion."""

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
import uuid

from sqlalchemy import String, and_, cast, func, or_, select

from app.db.fra_completion_models import (
    FRAArchiveRecord,
    FRAExtractionRun,
    FRAFieldReview,
    FRAImportBatch,
    FRAVillageProfile,
)
from app.db.fra_models import FRAClaim, FRADecision, FRATitle, GramSabha, RightsHolder
from app.db.models import Document, Parcel, User
from app.services.audit import record_audit
from app.services.concurrency import reserve_revision
from app.services.fra_claims import RIGHT_TYPES, add_geometry_version, create_claim
from app.services.parcel_normalization import convert_area_to_sqm
from app.services.parcel_resolver import ParcelLookup, ParcelResolver
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
LEGACY_STATUS_MAP = {
    "draft": "draft",
    "pending": "submitted",
    "received": "submitted",
    "submitted": "submitted",
    "under review": "submitted",
    "under_review": "submitted",
    "gram sabha verified": "gram_sabha_verified",
    "gram_sabha_verified": "gram_sabha_verified",
    "sdlc review": "sdlc_review",
    "sdlc_review": "sdlc_review",
    "dlc decided": "dlc_decided",
    "dlc_decided": "dlc_decided",
    "approved": "granted",
    "granted": "granted",
    "title issued": "granted",
    "title_issued": "granted",
    "rejected": "rejected",
    "remanded": "remanded",
    "withdrawn": "withdrawn",
}


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


def _location_key(record: FRAArchiveRecord, values: dict) -> str:
    return "|".join(
        _clean(value).casefold()
        for value in (
            record.state_code, values.get("district"), values.get("block"), values.get("village")
        )
    )


def _legacy_fingerprint(record: FRAArchiveRecord, values: dict) -> str:
    location = _location_key(record, values)
    survey = _clean(values.get("survey_number")).casefold()
    subdivision = _clean(values.get("subdivision_number")).casefold()
    claim_number = _clean(values.get("claim_number")).casefold()
    identity = (
        f"land|{location}|{values['right_type']}|{survey}|{subdivision}|"
        f"{_clean(values['holder_name']).casefold()}"
        if survey
        else f"claim|{record.state_code.casefold()}|{claim_number or record.legacy_reference.casefold()}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _normalized_date(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value.isoformat()
    cleaned = _clean(value)
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(cleaned, pattern).date().isoformat()
        except ValueError:
            continue
    raise ArchiveValidationError("Decision date must be a valid calendar date.")


def _positive_decimal(value, label: str) -> float:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ArchiveValidationError(f"{label} must be a positive number.") from error
    if not number.is_finite() or number <= 0:
        raise ArchiveValidationError(f"{label} must be a positive number.")
    return float(number)


def _normalized_area(values: dict, prefix: str) -> float | None:
    sqm_key = f"{prefix}_area_sqm"
    if values.get(sqm_key) not in (None, ""):
        return _positive_decimal(values[sqm_key], sqm_key.replace("_", " ").title())
    raw = _clean(values.get(f"{prefix}_area"))
    if not raw:
        return None
    explicit_unit = _clean(values.get("area_unit"))
    match = re.fullmatch(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[^\d]+)?", raw)
    if match is None:
        raise ArchiveValidationError(f"{prefix.title()} area requires a numeric value and unit.")
    unit = _clean(match.group("unit")) or explicit_unit
    if not unit:
        raise ArchiveValidationError(f"{prefix.title()} area requires a unit.")
    try:
        return convert_area_to_sqm(float(match.group("value")), unit)
    except ValueError as error:
        raise ArchiveValidationError(str(error)) from error


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
    entity_model_version_id=None,
    processing_time_ms: int | None = None,
    request_id: str | None = None,
) -> FRAExtractionRun:
    _require_user(session, actor_id)
    if record.state_code != "TN":
        get_state_profile(record.state_code)
    result = extractor.extract(record.legacy_reference, manifest)
    first_run = not record.extraction_runs
    reserve_revision(session, record, expected_revision=record.revision, state_field="review_state",
                     advance=not first_run,
                     conflict=ArchiveConflictError("The archive record changed during extraction."))
    run = FRAExtractionRun(
        archive_record=record,
        entity_model_version_id=entity_model_version_id,
        ocr_model_version=ocr_model_version,
        entity_model_version=result.model_version,
        raw_text=str(raw_text),
        standardized_json=dict(result.fields),
        field_evidence_json=dict(result.field_evidence),
        provenance_json={
            **dict(result.provenance),
            **({"warnings": list(result.warnings)} if result.warnings else {}),
        },
        overall_confidence=result.confidence,
        processing_time_ms=(
            processing_time_ms if processing_time_ms is not None else result.processing_time_ms
        ),
    )
    session.add(run)
    session.flush()
    default_method = _clean((result.provenance or {}).get("adapter")) or "entity_extraction"
    for field_name, extracted_value in result.fields.items():
        evidence = result.field_evidence.get(field_name, {})
        if not isinstance(evidence, dict):
            evidence = {"source_value": evidence}
        source_page = evidence.get("source_page", evidence.get("page"))
        if not isinstance(source_page, int) or isinstance(source_page, bool) or source_page < 1:
            source_page = None
        confidence = evidence.get("confidence", result.confidence)
        field_review = FRAFieldReview(
            extraction_run=run,
            field_name=str(field_name)[:100],
            source_page=source_page,
            source_value_json=evidence.get(
                "source_value", evidence.get("text", extracted_value)
            ),
            extracted_value_json=extracted_value,
            extraction_method=(
                _clean(evidence.get("extraction_method") or evidence.get("method"))
                or default_method
            )[:100],
            confidence=confidence,
            evidence_json=dict(evidence),
            review_state="pending",
        )
        session.add(field_review)
    record.review_state = "needs_review"
    if first_run:
        record.batch.processed_count += 1
    if record.batch.processed_count >= record.batch.record_count:
        record.batch.status = "needs_review"
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
    normalized_status = LEGACY_STATUS_MAP.get(reviewed["claim_status"])
    if normalized_status is None:
        raise ArchiveValidationError(
            "Claim status is not mapped to the native FRA lifecycle."
        )
    reviewed["normalized_claim_status"] = normalized_status
    for key in (
        "survey_number", "subdivision_number", "decision_authority", "title_number",
    ):
        if values.get(key) not in (None, ""):
            reviewed[key] = _clean(values[key])
    reviewed["decision_date"] = _normalized_date(values.get("decision_date"))
    for prefix in ("claimed", "granted"):
        normalized_area = _normalized_area(values, prefix)
        if normalized_area is not None:
            reviewed[f"{prefix}_area_sqm"] = normalized_area
    if reviewed.get("title_number") and normalized_status != "granted":
        raise ArchiveValidationError(
            "A mapped FRA title requires the reviewed legacy status to be granted."
        )
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
    reserve_revision(session, record, expected_revision=expected_revision, state_field="review_state",
                     conflict=ArchiveConflictError("The archive record changed since it was loaded."))
    record.reviewed_fields_json = reviewed
    record.duplicate_fingerprint = _legacy_fingerprint(record, reviewed)
    duplicate_ids = [
        str(item.id)
        for item in session.scalars(
            select(FRAArchiveRecord).where(
                FRAArchiveRecord.id != record.id,
                FRAArchiveRecord.duplicate_fingerprint == record.duplicate_fingerprint,
            )
        )
    ]
    record.provenance_json = {
        **dict(record.provenance_json or {}),
        "legacy_mapping_review": {
            "normalization_version": "fra-legacy-mapping-v1",
            "duplicate_record_ids": duplicate_ids,
        },
    }
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
    latest = record.latest_extraction
    if latest is not None:
        by_name = {item.field_name: item for item in latest.field_reviews}
        for field_name, final_value in reviewed.items():
            field_review = by_name.get(field_name)
            if field_review is None:
                field_review = FRAFieldReview(
                    extraction_run=latest,
                    field_name=str(field_name)[:100],
                    source_value_json=None,
                    extracted_value_json=None,
                    extraction_method="manual_review",
                    evidence_json={},
                )
                session.add(field_review)
                by_name[field_name] = field_review
            field_review.corrected_value_json = (
                final_value if final_value != field_review.extracted_value_json else None
            )
            field_review.final_value_json = final_value
            field_review.review_state = "approved"
            field_review.reviewed_by = reviewer_id
            field_review.reviewed_at = record.reviewed_at
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


def reject_archive_record(
    session,
    record: FRAArchiveRecord,
    *,
    reason: str,
    reviewer_id,
    expected_revision: int,
    request_id: str | None = None,
) -> FRAArchiveRecord:
    _require_user(session, reviewer_id, reviewer=True)
    if record.review_state not in {"needs_review", "reviewed"}:
        raise ArchiveConflictError("The archive record is not ready for review.")
    if record.revision != expected_revision:
        raise ArchiveConflictError("The archive record changed since it was loaded.")
    reason = _clean(reason)
    if not reason:
        raise ArchiveValidationError("An extraction rejection reason is required.")
    before = {"review_state": record.review_state, "revision": record.revision}
    reserve_revision(
        session, record, expected_revision=expected_revision, state_field="review_state",
        conflict=ArchiveConflictError("The archive record changed since it was loaded."),
    )
    reviewed_at = datetime.now(timezone.utc)
    record.review_state = "rejected"
    record.reviewed_by = reviewer_id
    record.reviewed_at = reviewed_at
    record.provenance_json = {
        **dict(record.provenance_json or {}),
        "extraction_rejection": {
            "reason": reason, "reviewed_by": str(reviewer_id), "reviewed_at": reviewed_at.isoformat(),
        },
    }
    latest = record.latest_extraction
    if latest is not None:
        for field_review in latest.field_reviews:
            field_review.review_state = "rejected"
            field_review.reviewed_by = reviewer_id
            field_review.reviewed_at = reviewed_at
    session.flush()
    record_audit(
        session, actor_id=reviewer_id, action="fra_archive_extraction_rejected",
        entity_type="fra_archive_record", entity_id=record.id, before=before,
        after={"review_state": "rejected", "revision": record.revision, "reason": reason},
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
    created_by=None,
) -> list[FRAArchiveRecord]:
    if offset < 0 or not 1 <= limit <= 200:
        raise ArchiveValidationError("Archive pagination is invalid.")
    statement = select(FRAArchiveRecord)
    if created_by is not None:
        statement = statement.join(FRAImportBatch).where(FRAImportBatch.created_by == created_by)
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


def _matched_village(session, record: FRAArchiveRecord, fields: dict):
    matches = list(session.scalars(select(FRAVillageProfile).where(
        func.lower(FRAVillageProfile.state_code) == record.state_code.casefold(),
        func.lower(FRAVillageProfile.district_name) == fields["district"].casefold(),
        func.lower(FRAVillageProfile.block_name) == fields["block"].casefold(),
        func.lower(FRAVillageProfile.village_name) == fields["village"].casefold(),
    )))
    if len(matches) > 1:
        raise ArchiveConflictError("Multiple canonical FRA villages match the reviewed record.")
    return matches[0] if matches else None


def _matched_or_created_holder(
    session, record: FRAArchiveRecord, fields: dict, gram_sabha: GramSabha
) -> tuple[RightsHolder, str]:
    holder_type = "individual" if fields["right_type"] == "IFR" else "community"
    location_key = _location_key(record, fields)
    candidates = list(session.scalars(select(RightsHolder).where(
        func.lower(RightsHolder.display_name) == fields["holder_name"].casefold(),
        RightsHolder.holder_type == holder_type,
    )))
    matches = [
        holder for holder in candidates
        if _clean((holder.metadata_json or {}).get("location_key")).casefold() == location_key
        or (
            holder.gram_sabha is not None
            and "|".join(_clean(value).casefold() for value in (
                record.state_code, holder.gram_sabha.district, holder.gram_sabha.block,
                holder.gram_sabha.village,
            )) == location_key
        )
    ]
    if len(matches) > 1:
        raise ArchiveConflictError("Multiple rights holders match the reviewed legacy record.")
    if matches:
        return matches[0], "exact_name_and_location"
    holder = RightsHolder(
        display_name=fields["holder_name"],
        holder_type=holder_type,
        claimant_category=fields.get("claimant_category"),
        external_reference=f"archive-record:{record.id}:rights-holder",
        gram_sabha=gram_sabha,
        metadata_json={
            "source": "archive_promotion",
            "archive_record_id": str(record.id),
            "location_key": location_key,
            "synthetic": record.synthetic,
        },
    )
    session.add(holder)
    session.flush()
    return holder, "created_from_reviewed_record"


def _matched_parcel(session, record: FRAArchiveRecord, fields: dict):
    result = ParcelResolver(session).resolve(ParcelLookup(
        state=get_state_profile(record.state_code).name,
        district=fields["district"],
        taluk=fields["block"],
        village=fields["village"],
        survey_number=_clean(fields.get("survey_number")),
        subdivision_number=_clean(fields.get("subdivision_number")),
        document_area_sqm=fields.get("claimed_area_sqm"),
        ocr_confidence=1.0,
    ))
    parcel = None
    if result.status == "matched" and result.parcel:
        parcel = session.get(Parcel, uuid.UUID(result.parcel["id"]))
    return parcel, {
        "status": result.status,
        "match_method": result.match_method,
        "match_confidence": result.match_confidence,
        "warnings": list(result.warnings),
        "missing_fields": list(result.missing_fields),
        "alternative_parcel_ids": [item["id"] for item in result.alternatives],
    }


def promote_archive_record(
    session,
    record: FRAArchiveRecord,
    *,
    actor_id,
    expected_revision: int,
    request_id: str | None = None,
) -> FRAClaim:
    _require_user(session, actor_id, reviewer=True)
    if record.revision != expected_revision:
        raise ArchiveConflictError("The archive record changed since it was loaded.")
    if record.promoted_claim_id is not None:
        existing = session.get(FRAClaim, record.promoted_claim_id)
        if existing is None:
            raise ArchiveConflictError("The promoted FRA claim no longer exists.")
        reserve_revision(session, record, expected_revision=expected_revision, state_field="review_state",
                         advance=False, conflict=ArchiveConflictError("The archive record changed since it was loaded."))
        return existing
    if record.review_state != "reviewed":
        raise ArchiveConflictError("Only a reviewed archive record can be promoted.")
    fields = _validated_review_fields(record, record.reviewed_fields_json)
    duplicate = session.scalar(select(FRAArchiveRecord).where(
        FRAArchiveRecord.id != record.id,
        FRAArchiveRecord.duplicate_fingerprint == record.duplicate_fingerprint,
        FRAArchiveRecord.promoted_claim_id.is_not(None),
    ))
    if duplicate is not None:
        raise ArchiveConflictError(
            "A duplicate reviewed legacy record has already been mapped to a native FRA claim."
        )
    claim_number = fields.get("claim_number") or record.legacy_reference
    existing_number = session.scalar(select(FRAClaim).where(FRAClaim.claim_number == claim_number))
    if existing_number is not None:
        raise ArchiveConflictError(
            "A native FRA claim already uses the reviewed legacy claim number."
        )
    reserve_revision(session, record, expected_revision=expected_revision, state_field="review_state",
                     conflict=ArchiveConflictError("The archive record changed since it was loaded."))
    gram_sabha = _gram_sabha_for_record(session, record)
    village = _matched_village(session, record, fields)
    holder, holder_match = _matched_or_created_holder(session, record, fields, gram_sabha)
    parcel, parcel_match = _matched_parcel(session, record, fields)
    mapping = {
        "version": "fra-legacy-mapping-v1",
        "reviewed_by": str(record.reviewed_by),
        "reviewed_at": record.reviewed_at.isoformat() if record.reviewed_at else None,
        "duplicate_fingerprint": record.duplicate_fingerprint,
        "holder_match": holder_match,
        "village_match": "exact" if village else "not_found",
        "village_id": str(village.id) if village else None,
        "parcel_match": parcel_match["status"],
        "parcel_id": str(parcel.id) if parcel else None,
        "parcel_evidence": parcel_match,
        "source_status": fields["claim_status"],
        "normalized_status": fields["normalized_claim_status"],
        "claim_year": fields.get("claim_year"),
    }
    claim = create_claim(
        session,
        claim_number=claim_number,
        right_type=fields["right_type"],
        rights_holder_id=holder.id,
        submitted_by=actor_id,
        gram_sabha_id=gram_sabha.id if fields["right_type"] in {"CR", "CFR"} else None,
        village_id=village.id if village else None,
        parcel_id=parcel.id if parcel else None,
        document_id=record.document_id,
        claimed_area_sqm=fields.get("claimed_area_sqm"),
        provenance={
            "source": "fra_archive_promotion",
            "archive_record_id": str(record.id),
            "legacy_reference": record.legacy_reference,
            "source_claim_status": fields["claim_status"],
            "source_provenance": dict(record.provenance_json or {}),
            "legacy_mapping": mapping,
            "synthetic": record.synthetic,
        },
        request_id=request_id,
    )
    geometry_version = None
    if parcel is not None:
        geometry_version = add_geometry_version(
            session,
            claim,
            geometry=parcel.geometry,
            source="reviewed_cadastral_match",
            provenance={
                "archive_record_id": str(record.id),
                "parcel_id": str(parcel.id),
                "parcel_source": parcel.source,
                "parcel_source_version": parcel.source_version,
                "parcel_source_record_id": parcel.source_record_id,
                "match": parcel_match,
                "legal_role": "supporting_spatial_evidence",
            },
            boundary_quality=parcel.boundary_quality,
            actor_id=actor_id,
            request_id=request_id,
        )
    target_status = fields["normalized_claim_status"]
    if target_status != "draft":
        decision = FRADecision(
            claim=claim,
            authority_level=fields.get("decision_authority") or "Reviewed legacy FRA record",
            from_status="legacy_record",
            to_status=target_status,
            outcome="historical_status_mapped",
            reasons_json=["Mapped from a reviewed legacy FRA source record."],
            decision_date=(date.fromisoformat(fields["decision_date"]) if fields.get("decision_date") else None),
            reference_number=fields.get("decision_reference"),
            actor_id=actor_id,
            request_id=request_id,
        )
        claim.status = target_status
        session.add(decision)
    if fields.get("title_number"):
        session.add(FRATitle(
            claim=claim,
            version=1,
            title_number=fields["title_number"],
            geometry_version_id=geometry_version.id if geometry_version else None,
            granted_area_sqm=(
                Decimal(str(fields["granted_area_sqm"]))
                if fields.get("granted_area_sqm") is not None else None
            ),
            active=True,
            metadata_json={
                "source": "reviewed_legacy_fra_record",
                "archive_record_id": str(record.id),
                "legal_role": "historical_title_mapping",
            },
            issued_by=actor_id,
        ))
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
