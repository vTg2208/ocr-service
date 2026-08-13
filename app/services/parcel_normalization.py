"""Deterministic, evidence-preserving parcel field normalization."""

from dataclasses import dataclass, field
import re
import unicodedata


@dataclass(frozen=True)
class ParcelReference:
    survey_number: str
    subdivision_number: str
    alternatives: list[str] = field(default_factory=list)
    needs_confirmation: bool = False


@dataclass(frozen=True)
class MatchConfidence:
    score: float
    reasons: list[str]


_REFERENCE = re.compile(
    r"^\s*(?P<survey>\d+)\s*(?:/|-)\s*(?P<subdivision>[A-Za-z0-9](?:\s*[A-Za-z0-9])*)\s*$"
)
_AMBIGUOUS = {"B": "8", "8": "B", "O": "0", "0": "O", "I": "1", "1": "I"}
_AREA_FACTORS = {
    "squaremetre": 1.0,
    "squaremetres": 1.0,
    "squaremeter": 1.0,
    "squaremeters": 1.0,
    "sqm": 1.0,
    "m2": 1.0,
    "hectare": 10_000.0,
    "hectares": 10_000.0,
    "ha": 10_000.0,
    "acre": 4046.8564224,
    "acres": 4046.8564224,
    "cent": 40.468564224,
    "cents": 40.468564224,
}


def parse_parcel_reference(value: str) -> ParcelReference:
    match = _REFERENCE.fullmatch(unicodedata.normalize("NFKC", value or ""))
    if not match:
        raise ValueError("A survey and subdivision identifier is required.")
    survey = match.group("survey")
    subdivision = re.sub(r"\s+", "", match.group("subdivision")).upper()
    alternatives: list[str] = []
    for index, character in enumerate(subdivision):
        replacement = _AMBIGUOUS.get(character)
        if replacement:
            alternative = subdivision[:index] + replacement + subdivision[index + 1 :]
            if alternative not in alternatives:
                alternatives.append(alternative)
    return ParcelReference(
        survey_number=survey,
        subdivision_number=subdivision,
        alternatives=alternatives,
        needs_confirmation=bool(alternatives),
    )


def normalize_identifier(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "")).upper()


def normalize_admin_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value or "").split()).casefold()


def normalize_admin_name(value: str, aliases: dict[str, str] | None = None) -> str:
    key = normalize_admin_key(value)
    normalized_aliases = {
        normalize_admin_key(alias): canonical for alias, canonical in (aliases or {}).items()
    }
    if key in normalized_aliases:
        return normalized_aliases[key]
    return " ".join(word.capitalize() for word in key.split())


def convert_area_to_sqm(value: float, unit: str) -> float:
    key = re.sub(r"[.\s²]", "", unicodedata.normalize("NFKC", unit or "")).casefold()
    try:
        factor = _AREA_FACTORS[key]
    except KeyError as exc:
        raise ValueError(f"Unsupported area unit: {unit}") from exc
    if value < 0:
        raise ValueError("Area must not be negative.")
    return float(value) * factor


def area_difference_percent(document_area: float | None, official_area: float | None) -> float | None:
    if document_area is None or official_area is None:
        return None
    if official_area <= 0:
        raise ValueError("Official area must be greater than zero.")
    return abs(float(document_area) - float(official_area)) / float(official_area) * 100


def calculate_match_confidence(
    *, exact_fields: int, total_fields: int, ocr_confidence: float,
    area_difference: float | None, area_tolerance: float,
) -> MatchConfidence:
    if total_fields <= 0 or not 0 <= exact_fields <= total_fields:
        raise ValueError("Field agreement counts are invalid.")
    normalized_ocr = ocr_confidence / 100 if ocr_confidence > 1 else ocr_confidence
    agreement = exact_fields / total_fields
    reasons = [f"exact_fields:{exact_fields}/{total_fields}"]
    area_score = 1.0
    if area_difference is not None:
        if area_difference <= area_tolerance:
            reasons.append("area_within_tolerance")
        else:
            reasons.append("area_outside_tolerance")
            area_score = max(0.0, 1.0 - area_difference / 100)
    score = agreement * 0.7 + max(0.0, min(1.0, normalized_ocr)) * 0.2 + area_score * 0.1
    return MatchConfidence(score=round(score, 4), reasons=reasons)
