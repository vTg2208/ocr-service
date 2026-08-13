"""Extract the parcel lookup key from OCR text using deterministic evidence."""

import re

from app.services.parcel_normalization import convert_area_to_sqm, parse_parcel_reference


_ADMIN = {
    field: re.compile(rf"\b{field}\s*:\s*(?P<value>[^\r\n]+)", re.IGNORECASE)
    for field in ("state", "district", "taluk", "village")
}
_SURVEY = re.compile(
    r"\b(?:survey\s*(?:no\.?|number)?|s\.?\s*no\.?)\s*[:.-]?\s*"
    r"(?P<reference>\d+[ \t]*(?:/|-)[ \t]*[A-Za-z0-9](?:[ \t]*[A-Za-z0-9])*)",
    re.IGNORECASE,
)
_AREA = re.compile(
    r"\b(?:extent|area)\s*[:.-]?\s*(?P<value>\d+(?:\.\d+){0,2})\s*"
    r"(?P<unit>square\s+met(?:er|re)s?|sq\.?\s*m|m[²2]|hectares?|ha|acres?|cents?)\b",
    re.IGNORECASE,
)


def extract_normalized_parcel_fields(text: str, ocr_confidence: float) -> dict:
    evidence = {}
    fields = {name: None for name in ("state", "district", "taluk", "village")}
    for name, pattern in _ADMIN.items():
        match = pattern.search(text)
        if match:
            fields[name] = " ".join(match.group("value").strip().split())
            evidence[name] = match.group(0).strip()
    survey_number = subdivision_number = None
    alternatives = []
    ambiguous_fields = []
    survey_match = _SURVEY.search(text)
    if survey_match:
        reference = parse_parcel_reference(survey_match.group("reference"))
        survey_number = reference.survey_number
        subdivision_number = reference.subdivision_number
        alternatives = reference.alternatives
        if reference.needs_confirmation:
            ambiguous_fields.append("subdivision_number")
        evidence["survey_number"] = survey_match.group(0).strip()
    document_area_sqm = None
    original_area = None
    area_match = _AREA.search(text)
    if area_match:
        raw_value = area_match.group("value")
        unit = area_match.group("unit")
        if raw_value.count(".") == 2 and unit.casefold() in {"hectare", "hectares", "ha"}:
            hectares, ares, square_metres = (int(part) for part in raw_value.split("."))
            document_area_sqm = float(hectares * 10_000 + ares * 100 + square_metres)
            original_value = raw_value
        else:
            original_value = float(raw_value)
            document_area_sqm = convert_area_to_sqm(original_value, unit)
        original_area = {"value": original_value, "unit": unit}
        evidence["area"] = area_match.group(0).strip()
    confidence = ocr_confidence / 100 if ocr_confidence > 1 else ocr_confidence
    return {
        **fields, "survey_number": survey_number,
        "subdivision_number": subdivision_number,
        "document_area_sqm": document_area_sqm, "original_area": original_area,
        "confidence": round(max(0.0, min(1.0, confidence)), 4), "evidence": evidence,
        "alternatives": alternatives, "ambiguous_fields": ambiguous_fields,
    }
