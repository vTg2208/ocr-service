"""Evidence-backed land record assembly and optional semantic enrichment."""

from dataclasses import asdict
import json
import re
import unicodedata

from app.config import get_settings
from app.models.response_models import (
    AreaField,
    CoordinateField,
    DocumentReference,
    EvidencedText,
    FieldEvidence,
    LandExtractionResult,
    LandLocation,
    LandRecord,
    OtherLandAttribute,
)
from app.services.land_candidates import LandCandidateSet, extract_land_candidates

settings = get_settings()

_LAND_EXTRACTION_INSTRUCTION = """You extract structured land records from OCR text.
Return one JSON object with a 'records' array and no prose.
Return one record per parcel. Use null or [] when evidence is absent.
Never infer or geocode coordinates. Use only coordinate candidates provided.
Never invent survey numbers, areas, dates, reference numbers, names, or locations.
Every populated semantic field must include an exact evidence_text substring from OCR_TEXT.
Each record may contain: survey_number, holder, holder_type, area, latitude, longitude,
location (village, taluk, district, state, address), land_type, uses_or_resources,
document_references, and other_attributes.
An evidenced value has value, evidence_text, and confidence from 0 to 1.
Area uses raw_value, evidence_text, and confidence. Return JSON only."""


def _deterministic_evidence(text: str) -> FieldEvidence:
    return FieldEvidence(
        text=text,
        method="deterministic",
        confidence=1.0,
        source_verified=False,
    )


def _shared_location(candidates: LandCandidateSet) -> LandLocation:
    fields = {}
    for candidate in candidates.locations:
        if candidate.kind not in fields:
            fields[candidate.kind] = EvidencedText(
                value=candidate.value,
                evidence=_deterministic_evidence(candidate.evidence_text),
            )
    return LandLocation(**fields)


def _shared_references(candidates: LandCandidateSet) -> list[DocumentReference]:
    references = [
        DocumentReference(
            kind="date",
            value=candidate.value,
            evidence=_deterministic_evidence(candidate.evidence_text),
        )
        for candidate in candidates.dates
    ]
    references.extend(
        DocumentReference(
            kind="reference_number",
            value=candidate.value,
            evidence=_deterministic_evidence(candidate.evidence_text),
        )
        for candidate in candidates.reference_numbers
    )
    return references


def build_deterministic_land_result(
    text: str,
) -> tuple[LandExtractionResult, LandCandidateSet]:
    """Build conservative parcel records without semantic inference."""
    candidates = extract_land_candidates(text)
    location = _shared_location(candidates)
    references = _shared_references(candidates)
    records = []

    for index, parcel in enumerate(candidates.parcels, start=1):
        evidence = _deterministic_evidence(parcel.evidence_text)
        records.append(
            LandRecord(
                record_id=f"land-{index}",
                survey_number=EvidencedText(
                    value=parcel.survey_number,
                    evidence=evidence,
                ),
                area=AreaField(
                    raw_value=parcel.area_raw,
                    unit=parcel.unit,
                    normalized_square_metres=parcel.normalized_square_metres,
                    evidence=evidence,
                )
                if parcel.area_raw
                else None,
                location=location.model_copy(deep=True),
                document_references=[item.model_copy(deep=True) for item in references],
                warnings=["Critical parcel values require source-document verification."],
            )
        )

    warnings = []
    if records:
        warnings.append(
            "Survey numbers and areas were extracted from OCR and are not source-verified."
        )

    if len(records) == 1 and len(candidates.coordinates) == 1:
        coordinate = candidates.coordinates[0]
        evidence = _deterministic_evidence(coordinate.evidence_text)
        records[0].latitude = CoordinateField(
            value=coordinate.latitude,
            format=coordinate.format,
            evidence=evidence,
        )
        records[0].longitude = CoordinateField(
            value=coordinate.longitude,
            format=coordinate.format,
            evidence=evidence,
        )
    elif candidates.coordinates:
        warnings.append(
            "Explicit coordinates were detected but could not be safely assigned to a parcel."
        )

    requires_review = bool(
        records
        or candidates.coordinates
        or candidates.locations
        or candidates.dates
        or candidates.reference_numbers
    )
    return (
        LandExtractionResult(
            status="partial",
            records=records,
            record_count=len(records),
            requires_human_review=requires_review,
            warnings=warnings,
        ),
        candidates,
    )


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split()).casefold()


def _supported_evidence(text: str, item: object) -> bool:
    if not isinstance(item, dict):
        return False
    value = item.get("value")
    evidence_text = item.get("evidence_text")
    confidence = item.get("confidence")
    if not isinstance(value, str) or not value.strip():
        return False
    if not isinstance(evidence_text, str) or not evidence_text.strip():
        return False
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return False
    normalized_evidence = _normalize(evidence_text)
    return (
        normalized_evidence in _normalize(text)
        and _normalize(value) in normalized_evidence
    )


def _llm_evidenced_text(item: dict) -> EvidencedText:
    return EvidencedText(
        value=item["value"].strip(),
        evidence=FieldEvidence(
            text=item["evidence_text"],
            method="llm_with_ocr_evidence",
            confidence=float(item["confidence"]),
            source_verified=False,
        ),
    )


class LandEnrichmentService:
    """Combine deterministic candidates with optional evidence-bound LLM output."""

    def __init__(self, client=None, model_name: str | None = None):
        self.client = client
        self.model_name = model_name or settings.llm_model_name

    async def extract(self, text: str) -> LandExtractionResult:
        deterministic, candidates = build_deterministic_land_result(text)
        if self.client is None:
            deterministic.status = "not_configured"
            deterministic.warnings.append(
                "LLM is not configured; holder and contextual fields were not inferred."
            )
            return deterministic

        try:
            payload = await self._request_payload(text, candidates)
            return self._merge_validated(text, candidates, deterministic, payload)
        except Exception:
            deterministic.status = "partial"
            deterministic.warnings.append(
                "Semantic land enrichment was unavailable; deterministic fields were retained."
            )
            return deterministic

    async def _request_payload(self, text: str, candidates: LandCandidateSet) -> dict:
        response = await self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": _LAND_EXTRACTION_INSTRUCTION},
                {
                    "role": "user",
                    "content": (
                        "OCR_TEXT:\n"
                        f"{text}\n\n"
                        "DETERMINISTIC_CANDIDATES:\n"
                        f"{json.dumps(asdict(candidates), ensure_ascii=False)}"
                    ),
                },
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        payload = json.loads(content)
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise ValueError("Land enrichment response did not contain a records array.")
        return payload

    def _merge_validated(
        self,
        text: str,
        candidates: LandCandidateSet,
        deterministic: LandExtractionResult,
        payload: dict,
    ) -> LandExtractionResult:
        records_by_survey = {
            record.survey_number.value: record
            for record in deterministic.records
            if record.survey_number is not None
        }
        llm_records = payload["records"]
        rejected = False

        for item in llm_records:
            if not isinstance(item, dict):
                rejected = True
                continue
            survey_number = item.get("survey_number")
            record = records_by_survey.get(survey_number)
            if record is None and survey_number is None and not deterministic.records:
                record = LandRecord(record_id="land-1")
                deterministic.records.append(record)
            if record is None:
                rejected = True
                continue

            supplied_area = item.get("area")
            if supplied_area is not None:
                supplied_raw = (
                    supplied_area.get("raw_value")
                    if isinstance(supplied_area, dict)
                    else None
                )
                if record.area is None or supplied_raw != record.area.raw_value:
                    rejected = True

            holder = item.get("holder")
            if holder is not None:
                if _supported_evidence(text, holder):
                    record.holder = _llm_evidenced_text(holder)
                    holder_type = item.get("holder_type") or holder.get("holder_type")
                    record.holder_type = (
                        holder_type
                        if holder_type in {"person", "organization", "unknown"}
                        else "unknown"
                    )
                else:
                    rejected = True

            location = item.get("location")
            if isinstance(location, dict):
                for field_name in ("village", "taluk", "district", "state", "address"):
                    value = location.get(field_name)
                    if value is None:
                        continue
                    if _supported_evidence(text, value):
                        if getattr(record.location, field_name) is None:
                            setattr(record.location, field_name, _llm_evidenced_text(value))
                    else:
                        rejected = True

            land_type = item.get("land_type")
            if land_type is not None:
                if _supported_evidence(text, land_type):
                    record.land_type = _llm_evidenced_text(land_type)
                else:
                    rejected = True

            for resource in item.get("uses_or_resources") or []:
                if _supported_evidence(text, resource):
                    record.uses_or_resources.append(_llm_evidenced_text(resource))
                else:
                    rejected = True

            rejected = self._merge_coordinates(
                text,
                candidates,
                record,
                item,
            ) or rejected

            for reference in item.get("document_references") or []:
                if not _supported_evidence(text, reference):
                    rejected = True
                    continue
                kind = str(reference.get("kind") or "reference")
                record.document_references.append(
                    DocumentReference(
                        kind=kind,
                        value=reference["value"],
                        evidence=_llm_evidenced_text(reference).evidence,
                    )
                )

            for attribute in item.get("other_attributes") or []:
                if not _supported_evidence(text, attribute):
                    rejected = True
                    continue
                value = attribute["value"]
                if re.search(r"\d", value) and not self._known_numeric_value(
                    value, candidates
                ):
                    rejected = True
                    continue
                record.other_attributes.append(
                    OtherLandAttribute(
                        key=str(attribute.get("key") or "other"),
                        value=value,
                        evidence=_llm_evidenced_text(attribute).evidence,
                    )
                )

        deterministic.status = "completed"
        deterministic.record_count = len(deterministic.records)
        deterministic.requires_human_review = bool(deterministic.records)
        if rejected:
            deterministic.warnings.append(
                "One or more unsupported LLM fields were rejected."
            )
        return deterministic

    @staticmethod
    def _known_numeric_value(value: str, candidates: LandCandidateSet) -> bool:
        known = {candidate.survey_number for candidate in candidates.parcels}
        known.update(
            candidate.area_raw
            for candidate in candidates.parcels
            if candidate.area_raw is not None
        )
        known.update(candidate.value for candidate in candidates.dates)
        known.update(candidate.value for candidate in candidates.reference_numbers)
        return value in known

    @staticmethod
    def _merge_coordinates(
        text: str,
        candidates: LandCandidateSet,
        record: LandRecord,
        item: dict,
    ) -> bool:
        latitude = item.get("latitude")
        longitude = item.get("longitude")
        if latitude is None and longitude is None:
            return False
        if not _supported_evidence(text, latitude) or not _supported_evidence(
            text, longitude
        ):
            return True
        try:
            lat_value = float(latitude["value"])
            lon_value = float(longitude["value"])
        except (TypeError, ValueError):
            return True
        candidate = next(
            (
                value
                for value in candidates.coordinates
                if abs(value.latitude - lat_value) < 1e-7
                and abs(value.longitude - lon_value) < 1e-7
            ),
            None,
        )
        if candidate is None:
            return True
        evidence = FieldEvidence(
            text=candidate.evidence_text,
            method="hybrid",
            confidence=min(
                float(latitude["confidence"]),
                float(longitude["confidence"]),
            ),
            source_verified=False,
        )
        record.latitude = CoordinateField(
            value=candidate.latitude,
            format=candidate.format,
            evidence=evidence,
        )
        record.longitude = CoordinateField(
            value=candidate.longitude,
            format=candidate.format,
            evidence=evidence,
        )
        return False
