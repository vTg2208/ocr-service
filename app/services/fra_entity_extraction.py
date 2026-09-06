"""Tamil Nadu FRA entity extraction with page-level source evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
import re
import time

from app.services.model_gateway import (
    EntityExtractionResult,
    ModelOutputValidationError,
    validate_model_output,
)
from app.services.state_profiles import get_state_profile


FIELD_LABELS = {
    "claim_number": (
        "Claim No", "Claim Number", "Application No", "Application Number",
        "கோரிக்கை எண்", "விண்ணப்ப எண்",
    ),
    "holder_name": (
        "Claimant", "Claimant Name", "Rights Holder", "Holder Name",
        "கோரிக்கையாளர்", "உரிமையாளர் பெயர்", "உரிமையாளர்கள் பெயர்",
    ),
    "household_name": ("Household", "Household Name", "குடும்பத்தின் பெயர்"),
    "community_name": ("Community", "Community Name", "சமூகத்தின் பெயர்"),
    "district": ("District", "மாவட்டம்"),
    "block": ("Block", "Taluk", "Tehsil", "வட்டம்", "ஒன்றியம்"),
    "village": ("Village", "Revenue Village", "கிராமம்", "வருவாய் கிராமம்"),
    "survey_number": ("Survey No", "Survey Number", "Sy No", "புல எண்", "சர்வே எண்"),
    "subdivision_number": (
        "Subdivision", "Subdivision No", "Subdivision Number", "Sub Division",
        "Sub Division No", "உட்பிரிவு", "உட்பிரிவு எண்",
    ),
    "right_type": (
        "Right Type", "Rights Type", "Claim Type", "FRA Right", "உரிமை வகை",
        "கோரிக்கை வகை",
    ),
    "claimed_area": ("Claimed Area", "Area Claimed", "கோரிய பரப்பளவு"),
    "granted_area": ("Granted Area", "Area Granted", "அனுமதிக்கப்பட்ட பரப்பளவு"),
    "claim_status": ("Claim Status", "Application Status", "Status", "நிலை"),
    "gram_sabha_status": (
        "Gram Sabha", "Gram Sabha Status", "Gram Sabha Decision", "கிராம சபை நிலை",
        "கிராம சபை முடிவு",
    ),
    "sdlc_status": ("SDLC", "SDLC Status", "SDLC Decision", "எஸ்டிஎல்சி நிலை"),
    "dlc_status": ("DLC", "DLC Status", "DLC Decision", "டிஎல்சி நிலை"),
    "decision_authority": (
        "Decision Authority", "Deciding Authority", "Decision By",
        "முடிவு அதிகாரம்", "தீர்மான அதிகாரம்",
    ),
    "decision_date": ("Decision Date", "Order Date", "முடிவு தேதி", "ஆணை தேதி"),
    "title_number": (
        "FRA Title No", "FRA Title Number", "RoFR No", "RoFR Number",
        "வன உரிமை பட்டா எண்", "வன உரிமை ஆவண எண்",
    ),
    "coordinates": (
        "Coordinates", "Latitude Longitude", "Lat Long", "அட்சரேகை தீர்க்கரேகை",
    ),
    "latitude": ("Latitude", "Lat", "அட்சரேகை"),
    "longitude": ("Longitude", "Lon", "Lng", "தீர்க்கரேகை"),
    "claim_year": ("Claim Year", "Application Year", "Year", "கோரிக்கை ஆண்டு", "ஆண்டு"),
}
REQUIRED_REVIEW_FIELDS = (
    "claim_number", "holder_name", "district", "block", "village", "right_type",
    "claim_status",
)

AREA_UNIT_FACTORS = {
    "sqm": 1.0,
    "sq m": 1.0,
    "m2": 1.0,
    "square metre": 1.0,
    "square metres": 1.0,
    "square meter": 1.0,
    "square meters": 1.0,
    "சதுர மீட்டர்": 1.0,
    "ha": 10000.0,
    "hectare": 10000.0,
    "hectares": 10000.0,
    "ஹெக்டேர்": 10000.0,
    "acre": 4046.8564224,
    "acres": 4046.8564224,
    "ac": 4046.8564224,
    "ஏக்கர்": 4046.8564224,
    "cent": 40.468564224,
    "cents": 40.468564224,
    "சென்ட்": 40.468564224,
}


def _label_pattern(labels: tuple[str, ...]) -> re.Pattern:
    choices = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    return re.compile(rf"^\s*(?:{choices})\s*[:：-]\s*(?P<value>.+?)\s*$", re.IGNORECASE)


PATTERNS = {field: _label_pattern(labels) for field, labels in FIELD_LABELS.items()}


def _label_only_pattern(labels: tuple[str, ...]) -> re.Pattern:
    choices = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    return re.compile(rf"^\s*(?:{choices})\s*[:：-]?\s*$", re.IGNORECASE)


LABEL_ONLY_PATTERNS = {
    field: _label_only_pattern(labels) for field, labels in FIELD_LABELS.items()
}


def _confidence(value, *, default=None):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelOutputValidationError("OCR confidence must be numeric.")
    normalized = float(value)
    if normalized > 1:
        normalized /= 100
    if not 0 <= normalized <= 1:
        raise ModelOutputValidationError("OCR confidence must be between 0 and 1.")
    return normalized


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _area_sqm(value: str) -> float | None:
    cleaned = _clean(value).casefold().replace(",", "")
    units = "|".join(re.escape(unit) for unit in sorted(AREA_UNIT_FACTORS, key=len, reverse=True))
    match = re.fullmatch(rf"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>{units})", cleaned)
    if match is None:
        return None
    amount = float(match.group("value"))
    if amount <= 0:
        return None
    return round(amount * AREA_UNIT_FACTORS[match.group("unit")], 4)


def _normalized_value(field: str, value: str):
    cleaned = _clean(value)
    if field in {"claim_status", "gram_sabha_status", "sdlc_status", "dlc_status"}:
        return cleaned.casefold() or None
    if field == "right_type":
        key = re.sub(r"[^A-Z]", "", cleaned.upper())
        aliases = {
            "IFR": "IFR", "INDIVIDUALFORESTRIGHT": "IFR", "INDIVIDUALFORESTRIGHTS": "IFR",
            "CR": "CR", "COMMUNITYRIGHT": "CR", "COMMUNITYRIGHTS": "CR",
            "CFR": "CFR", "COMMUNITYFORESTRESOURCE": "CFR",
            "COMMUNITYFORESTRESOURCERIGHT": "CFR", "COMMUNITYFORESTRESOURCERIGHTS": "CFR",
        }
        return aliases.get(key)
    if field == "decision_date":
        for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
            try:
                return datetime.strptime(cleaned, pattern).date().isoformat()
            except ValueError:
                continue
        return None
    if field == "claim_year":
        if re.fullmatch(r"\d{4}", cleaned):
            year = int(cleaned)
            if 1900 <= year <= datetime.now(timezone.utc).year:
                return year
        return None
    if field in {"latitude", "longitude"}:
        try:
            number = float(cleaned)
        except ValueError:
            return None
        limit = 90 if field == "latitude" else 180
        return number if -limit <= number <= limit else None
    if field == "coordinates":
        match = re.fullmatch(
            r"\s*([-+]?\d+(?:\.\d+)?)\s*[,; ]\s*([-+]?\d+(?:\.\d+)?)\s*",
            cleaned,
        )
        if not match:
            return None
        latitude, longitude = float(match.group(1)), float(match.group(2))
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return None
        return f"{latitude:g}, {longitude:g}"
    return cleaned or None


def _page_inputs(manifest: dict) -> list[dict]:
    pages = manifest.get("pages")
    if pages is None:
        raw_text = manifest.get("raw_text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ModelOutputValidationError("OCR text or page results are required for FRA entity extraction.")
        return [{
            "page_number": 1,
            "text": raw_text,
            "confidence": _confidence(manifest.get("ocr_confidence")),
        }]
    if not isinstance(pages, list) or not pages:
        raise ModelOutputValidationError("OCR pages must be a non-empty array.")
    normalized = []
    seen = set()
    document_confidence = _confidence(manifest.get("ocr_confidence"))
    for page in pages:
        if not isinstance(page, dict):
            raise ModelOutputValidationError("Each OCR page must be an object.")
        number = page.get("page_number")
        text = page.get("text")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1 or number in seen:
            raise ModelOutputValidationError("OCR page numbers must be unique positive integers.")
        if not isinstance(text, str):
            raise ModelOutputValidationError("OCR page text must be a string.")
        lines = page.get("lines") or []
        if not isinstance(lines, list):
            raise ModelOutputValidationError("OCR page lines must be an array.")
        normalized_lines = []
        for line in lines:
            if not isinstance(line, dict) or not isinstance(line.get("text"), str):
                raise ModelOutputValidationError("Each OCR line must contain text.")
            box = line.get("bounding_box")
            if box is not None:
                if (
                    not isinstance(box, (list, tuple)) or len(box) != 4
                    or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)) for value in box)
                ):
                    raise ModelOutputValidationError("OCR line bounding boxes require four finite numbers.")
                box = [float(value) for value in box]
                if box[2] <= box[0] or box[3] <= box[1]:
                    raise ModelOutputValidationError("OCR line bounding boxes must have positive dimensions.")
            normalized_lines.append({
                "text": line["text"],
                "confidence": _confidence(line.get("confidence"), default=_confidence(page.get("confidence"), default=document_confidence)),
                "bounding_box": box,
            })
        seen.add(number)
        normalized.append({
            "page_number": number,
            "text": text,
            "confidence": _confidence(page.get("confidence"), default=document_confidence),
            "lines": normalized_lines,
        })
    return normalized


def _is_label(text: str) -> bool:
    return any(pattern.match(text) for pattern in PATTERNS.values()) or any(
        pattern.match(text) for pattern in LABEL_ONLY_PATTERNS.values()
    )


def _plausible_layout_value(field: str, text: str) -> bool:
    value = _clean(text)
    if not value or len(value) > 255 or _is_label(value):
        return False
    if field == "survey_number":
        return bool(re.fullmatch(r"[A-Za-z0-9./-]{1,40}", value) and any(char.isdigit() for char in value))
    if field == "subdivision_number":
        return bool(re.fullmatch(r"[A-Za-z0-9./-]{1,40}", value))
    if field in {"claim_year", "latitude", "longitude", "coordinates", "decision_date", "right_type"}:
        return _normalized_value(field, value) is not None
    return True


def _layout_candidates(page: dict) -> list[tuple[str, dict]]:
    lines = [line for line in page.get("lines", []) if line.get("bounding_box")]
    results = []
    for label in lines:
        label_text = _clean(label["text"])
        field = next(
            (name for name, pattern in LABEL_ONLY_PATTERNS.items() if pattern.match(label_text)),
            None,
        )
        if field is None:
            continue
        left, top, right, bottom = label["bounding_box"]
        label_height = bottom - top
        label_center_y = (top + bottom) / 2
        options = []
        for value_line in lines:
            if value_line is label or not _plausible_layout_value(field, value_line["text"]):
                continue
            value_left, value_top, value_right, value_bottom = value_line["bounding_box"]
            value_center_y = (value_top + value_bottom) / 2
            row_tolerance = max(label_height, value_bottom - value_top) * 0.8
            if abs(value_center_y - label_center_y) <= row_tolerance and value_left >= right - 5:
                score = abs(value_center_y - label_center_y) * 5 + max(0, value_left - right)
                options.append((0, score, value_line))
                continue
            horizontal_overlap = max(0, min(right, value_right) - max(left, value_left))
            if value_top > bottom and horizontal_overlap > 0 and value_top - bottom <= max(300, label_height * 15):
                score = (value_top - bottom) + abs(((left + right) / 2) - ((value_left + value_right) / 2))
                options.append((1, score, value_line))
        if not options:
            continue
        _priority, _score, selected = min(options, key=lambda item: (item[0], item[1]))
        confidence_values = [value for value in (label.get("confidence"), selected.get("confidence")) if value is not None]
        confidence = round(min(confidence_values) * 0.8, 5) if confidence_values else 0.65
        results.append((field, {
            "raw_value": _clean(selected["text"]),
            "text": f"{label_text}: {_clean(selected['text'])}",
            "line": None,
            "source_page": page["page_number"],
            "confidence": confidence,
            "extraction_method": "fra_layout_ner",
            "label_bounding_box": label["bounding_box"],
            "source_bounding_box": selected["bounding_box"],
        }))
    return results


class TamilNaduFRAExtractor:
    """Allow-listed Tamil/English FRA label NER with explicit ambiguity handling."""

    def __init__(self, version: str):
        self.version = version.strip()
        if not self.version:
            raise ModelOutputValidationError("A model version is required.")

    def extract(self, document_reference: str, manifest: dict) -> EntityExtractionResult:
        if not document_reference.strip():
            raise ModelOutputValidationError("A document reference is required.")
        if not isinstance(manifest, dict):
            raise ModelOutputValidationError("Extraction input must be an object.")
        return self._extract_pages(_page_inputs(manifest), document_reference=document_reference)

    def extract_text(
        self,
        raw_text: str,
        *,
        document_reference: str = "in-memory-text",
    ) -> EntityExtractionResult:
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ModelOutputValidationError("OCR text is required for FRA entity extraction.")
        return self._extract_pages(
            [{"page_number": 1, "text": raw_text, "confidence": None}],
            document_reference=document_reference,
        )

    def _extract_pages(self, pages: list[dict], *, document_reference: str) -> EntityExtractionResult:
        started = time.perf_counter()
        candidates: dict[str, list[dict]] = {field: [] for field in PATTERNS}
        for page in pages:
            page_lines = page.get("lines") or [
                {"text": line, "confidence": page["confidence"], "bounding_box": None}
                for line in page["text"].splitlines()
            ]
            for line_number, line in enumerate(page_lines, start=1):
                clean_line = line["text"].strip()
                if not clean_line:
                    continue
                for field, pattern in PATTERNS.items():
                    match = pattern.match(clean_line)
                    if match is None:
                        continue
                    raw_value = _clean(match.group("value"))
                    if raw_value:
                        candidates[field].append({
                            "raw_value": raw_value,
                            "text": clean_line,
                            "line": line_number,
                            "source_page": page["page_number"],
                            "confidence": round((line.get("confidence") if line.get("confidence") is not None else page["confidence"] if page["confidence"] is not None else 1.0) * 0.98, 5),
                            "extraction_method": "fra_label_ner",
                            "source_bounding_box": line.get("bounding_box"),
                        })
                    break
            for field, candidate in _layout_candidates(page):
                candidates[field].append(candidate)

        fields = {}
        evidence = {}
        warnings = []
        ambiguous_fields = []
        invalid_fields = []
        for field, found in candidates.items():
            if not found:
                continue
            normalized = []
            for candidate in found:
                value = _normalized_value(field, candidate["raw_value"])
                if value is not None:
                    normalized.append({**candidate, "value": value})
            if not normalized:
                fields[field] = None
                invalid_fields.append(field)
                warnings.append(f"Invalid {field}; reviewer correction required")
                first = found[0]
                evidence[field] = {
                    **first,
                    "source_value": first["raw_value"],
                    "extraction_method": first.get("extraction_method", "fra_label_ner"),
                    "validation_error": True,
                    "candidates": [
                        {"source_value": item["raw_value"], "source_page": item["source_page"], "line": item["line"]}
                        for item in found
                    ],
                }
                continue
            unique = []
            seen_values = set()
            for candidate in normalized:
                marker = str(candidate["value"]).casefold()
                if marker not in seen_values:
                    seen_values.add(marker)
                    unique.append(candidate)
            selected = max(unique, key=lambda item: item["confidence"])
            evidence[field] = {
                "text": selected["text"],
                "line": selected["line"],
                "source_page": selected["source_page"],
                "source_value": selected["raw_value"],
                "confidence": selected["confidence"],
                "extraction_method": selected.get("extraction_method", "fra_label_ner"),
                "ambiguous": len(unique) > 1,
                "candidates": [
                    {
                        "value": item["value"],
                        "source_value": item["raw_value"],
                        "source_page": item["source_page"],
                        "line": item["line"],
                        "confidence": item["confidence"],
                        "extraction_method": item.get("extraction_method", "fra_label_ner"),
                    }
                    for item in unique
                ],
            }
            if len(unique) > 1:
                fields[field] = None
                ambiguous_fields.append(field)
                warnings.append(f"Ambiguous {field}; reviewer selection required")
            else:
                fields[field] = selected["value"]

        if fields.get("coordinates"):
            latitude, longitude = [float(value.strip()) for value in fields["coordinates"].split(",")]
            fields.setdefault("latitude", latitude)
            fields.setdefault("longitude", longitude)
            for field, value in (("latitude", latitude), ("longitude", longitude)):
                if field not in evidence:
                    evidence[field] = {
                        **evidence["coordinates"],
                        "source_value": evidence["coordinates"]["source_value"],
                        "extraction_method": "coordinate_pair_parser",
                        "derived_value": value,
                    }

        for source_field, normalized_field in (
            ("claimed_area", "claimed_area_sqm"),
            ("granted_area", "granted_area_sqm"),
        ):
            if not fields.get(source_field):
                continue
            normalized_area = _area_sqm(str(fields[source_field]))
            if normalized_area is None:
                warnings.append(
                    f"Unnormalized {source_field}; reviewer must confirm the value and unit"
                )
                evidence[source_field]["normalization_warning"] = "unsupported_or_missing_area_unit"
                continue
            fields[normalized_field] = normalized_area
            evidence[normalized_field] = {
                **evidence[source_field],
                "extraction_method": "area_unit_parser",
                "derived_from": source_field,
                "derived_value": normalized_area,
            }

        profile = get_state_profile("TN")
        fields["state"] = profile.name
        fields["state_code"] = profile.code
        evidence["state"] = {
            "source_page": None, "source_value": None, "confidence": 1.0,
            "extraction_method": "intake_context",
        }
        evidence["state_code"] = {
            "source_page": None, "source_value": profile.name, "confidence": 1.0,
            "extraction_method": "state_profile_mapping",
        }
        for field, normalizer in (
            ("district", profile.normalize_district),
            ("block", profile.normalize_block),
            ("village", profile.normalize_village),
        ):
            if fields.get(field):
                fields[field] = normalizer(str(fields[field]))

        validate_model_output(fields)
        missing = [field for field in REQUIRED_REVIEW_FIELDS if not fields.get(field)]
        for field in missing:
            if field not in ambiguous_fields and field not in invalid_fields:
                warnings.append(f"Missing {field}")
        scored = [
            item["confidence"] for name, item in evidence.items()
            if name not in {"state", "state_code"} and fields.get(name) is not None
        ]
        overall_confidence = round(sum(scored) / len(scored), 5) if scored else None
        elapsed = max(0, round((time.perf_counter() - started) * 1000))
        return EntityExtractionResult(
            fields=fields,
            field_evidence=evidence,
            confidence=overall_confidence,
            model_version=self.version,
            processing_time_ms=elapsed,
            provenance={
                "adapter": "local_python",
                "runner": "tamil_nadu_fra_regex_v1",
                "state_code": "TN",
                "document_reference": document_reference,
                "page_count": len(pages),
                "ocr_pages": [
                    {
                        "page_number": page["page_number"],
                        "confidence": page.get("confidence"),
                        "line_count": len(page.get("lines") or [
                            line for line in page["text"].splitlines() if line.strip()
                        ]),
                    }
                    for page in pages
                ],
                "missing_fields": missing,
                "ambiguous_fields": ambiguous_fields,
                "invalid_fields": invalid_fields,
                "legal_role": "unverified_extraction",
            },
            warnings=warnings,
        )


__all__ = ["TamilNaduFRAExtractor"]
