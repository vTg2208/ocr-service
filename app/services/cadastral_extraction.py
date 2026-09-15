"""Extract the parcel lookup key from OCR text using deterministic evidence."""

import re

from app.services.parcel_normalization import convert_area_to_sqm, parse_parcel_reference


_ADMIN = {
    field: re.compile(rf"\b{field}\s*:\s*(?P<value>[^\r\n]+)", re.IGNORECASE)
    for field in ("state", "district", "taluk", "village")
}
_TAMIL_ADMIN = {
    "district": re.compile(r"(?m)^\s*மாவட்டம்\s*:\s*(?P<value>[^\r\n]+)"),
    "taluk": re.compile(r"(?m)^\s*வட்டம்\s*:\s*(?P<value>[^\r\n]+)"),
    "village": re.compile(r"(?m)^\s*(?:வருவாய்\s+)?கிராமம்\s*:\s*(?P<value>[^\r\n]+)"),
}
_TAMIL_NADU = re.compile(r"தமிழ்நாடு\s+அரசு")
_SURVEY = re.compile(
    r"\b(?:survey\s*(?:no\.?|number)?|s\.?\s*no\.?)\s*[:.-]?\s*"
    r"(?P<reference>\d+[ \t]*(?:/|-)[ \t]*[A-Za-z0-9](?:[ \t]*[A-Za-z0-9])*)",
    re.IGNORECASE,
)
_TAMIL_TABLE_ROW = re.compile(
    r"(?m)^\s*(?P<survey>\d{1,4})\s*\r?\n"
    r"\s*(?P<subdivision>\d*[A-Za-z][A-Za-z0-9]*)\s*\r?\n"
    r"\s*(?P<extent>\d+\s*[-–—]\s*\d{1,2}\.\d{1,2})\s*(?:\r?\n|$)"
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
    if not fields["state"] and (state_match := _TAMIL_NADU.search(text)):
        fields["state"] = "தமிழ்நாடு"
        evidence["state"] = state_match.group(0)
    for name, pattern in _TAMIL_ADMIN.items():
        if fields[name]:
            continue
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
    tamil_table_match = None
    if not survey_match and "புல எண்" in text and "உட்பிரிவு" in text:
        tamil_table_match = _TAMIL_TABLE_ROW.search(text)
        if tamil_table_match:
            reference = parse_parcel_reference(
                f"{tamil_table_match.group('survey')}/{tamil_table_match.group('subdivision')}"
            )
            survey_number = reference.survey_number
            subdivision_number = reference.subdivision_number
            alternatives = reference.alternatives
            if reference.needs_confirmation:
                ambiguous_fields.append("subdivision_number")
            evidence["survey_number"] = (
                f"{tamil_table_match.group('survey')} / {tamil_table_match.group('subdivision')}"
            )
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
    elif tamil_table_match:
        extent = tamil_table_match.group("extent")
        hectares, ares = re.split(r"\s*[-–—]\s*", extent)
        document_area_sqm = float(hectares) * 10_000 + float(ares) * 100
        original_area = {"value": extent, "unit": "hectare-are"}
        evidence["area"] = extent
    confidence = ocr_confidence / 100 if ocr_confidence > 1 else ocr_confidence
    return {
        **fields, "survey_number": survey_number,
        "subdivision_number": subdivision_number,
        "document_area_sqm": document_area_sqm, "original_area": original_area,
        "confidence": round(max(0.0, min(1.0, confidence)), 4), "evidence": evidence,
        "alternatives": alternatives, "ambiguous_fields": ambiguous_fields,
    }
