"""Deterministic candidate extraction for land-related OCR text."""

from dataclasses import dataclass, field
import re
from typing import Literal


@dataclass(frozen=True)
class TextCandidate:
    value: str
    evidence_text: str
    start: int
    end: int


@dataclass(frozen=True)
class SurveyAreaCandidate:
    survey_number: str
    area_raw: str | None
    unit: str | None
    normalized_square_metres: float | None
    evidence_text: str
    start: int
    end: int


@dataclass(frozen=True)
class CoordinatePairCandidate:
    latitude: float
    longitude: float
    format: Literal["decimal", "dms"]
    evidence_text: str
    start: int
    end: int


@dataclass(frozen=True)
class LocationCandidate:
    kind: Literal["village", "taluk", "district", "state", "address"]
    value: str
    evidence_text: str
    start: int
    end: int


@dataclass
class LandCandidateSet:
    parcels: list[SurveyAreaCandidate] = field(default_factory=list)
    coordinates: list[CoordinatePairCandidate] = field(default_factory=list)
    dates: list[TextCandidate] = field(default_factory=list)
    reference_numbers: list[TextCandidate] = field(default_factory=list)
    locations: list[LocationCandidate] = field(default_factory=list)


_SURVEY_AREA_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<survey>\d{1,4}/[A-Za-z0-9]+)"
    r"\s*[([]\s*(?P<area>\d{1,2}\.\d{2}\.\d{1,2})\s*[)\]]"
    r"(?:\s*(?P<unit>hectares?|ha|acres?|square\s+met(?:er|re)s?|sq\.?\s*m))?",
    re.IGNORECASE,
)
_DECIMAL_COORDINATE_PATTERN = re.compile(
    r"\b(?:lat(?:itude)?)\s*[:=]?\s*(?P<lat>[+-]?\d{1,3}(?:\.\d+)?)"
    r"\s*[,;]?\s*"
    r"(?:lon(?:gitude)?)\s*[:=]?\s*(?P<lon>[+-]?\d{1,3}(?:\.\d+)?)",
    re.IGNORECASE,
)
_DMS_COORDINATE_PATTERN = re.compile(
    r"(?P<lat_deg>\d{1,2})\s*°\s*(?P<lat_min>\d{1,2})\s*['′]\s*"
    r"(?P<lat_sec>\d{1,2}(?:\.\d+)?)\s*[\"″]?\s*(?P<lat_dir>[NS])"
    r"\s*[,;]?\s*"
    r"(?P<lon_deg>\d{1,3})\s*°\s*(?P<lon_min>\d{1,2})\s*['′]\s*"
    r"(?P<lon_sec>\d{1,2}(?:\.\d+)?)\s*[\"″]?\s*(?P<lon_dir>[EW])",
    re.IGNORECASE,
)
_DATE_PATTERN = re.compile(r"(?<!\d)\d{2}\.\d{2}\.\d{4}(?!\d)")
_REFERENCE_PATTERN = re.compile(
    r"\b(?:ref(?:erence)?|no\.?)\s*(?:no\.?)?\s*[:.-]?\s*"
    r"(?P<value>\d{1,4}/[A-Za-z0-9]+(?:/\d{2,4})?)",
    re.IGNORECASE,
)
_LOCATION_PATTERN = re.compile(
    r"(?P<value>[A-Za-z][A-Za-z .'-]{0,80}?)\s+"
    r"(?P<kind>Village|Taluk|District|State)\b",
    re.IGNORECASE,
)


def _dms_to_decimal(
    degrees: float,
    minutes: float,
    seconds: float,
    direction: str,
) -> float:
    value = degrees + minutes / 60 + seconds / 3600
    return -value if direction.upper() in {"S", "W"} else value


def _normalize_area(raw_value: str, unit: str | None) -> float | None:
    if not unit:
        return None
    normalized_unit = unit.lower().replace(".", "").replace(" ", "")
    if normalized_unit not in {"hectare", "hectares", "ha"}:
        return None
    hectares, ares, square_metres = (int(part) for part in raw_value.split("."))
    return float(hectares * 10000 + ares * 100 + square_metres)


def _valid_coordinate_pair(latitude: float, longitude: float) -> bool:
    return -90 <= latitude <= 90 and -180 <= longitude <= 180


def extract_land_candidates(text: str) -> LandCandidateSet:
    """Extract only explicit, source-locatable land candidates."""
    candidates = LandCandidateSet()

    for match in _SURVEY_AREA_PATTERN.finditer(text):
        unit = match.group("unit")
        area_raw = match.group("area")
        candidates.parcels.append(
            SurveyAreaCandidate(
                survey_number=match.group("survey"),
                area_raw=area_raw,
                unit=unit,
                normalized_square_metres=_normalize_area(area_raw, unit),
                evidence_text=match.group(0),
                start=match.start(),
                end=match.end(),
            )
        )

    for match in _DECIMAL_COORDINATE_PATTERN.finditer(text):
        latitude = float(match.group("lat"))
        longitude = float(match.group("lon"))
        if _valid_coordinate_pair(latitude, longitude):
            candidates.coordinates.append(
                CoordinatePairCandidate(
                    latitude=latitude,
                    longitude=longitude,
                    format="decimal",
                    evidence_text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
            )

    for match in _DMS_COORDINATE_PATTERN.finditer(text):
        latitude = _dms_to_decimal(
            float(match.group("lat_deg")),
            float(match.group("lat_min")),
            float(match.group("lat_sec")),
            match.group("lat_dir"),
        )
        longitude = _dms_to_decimal(
            float(match.group("lon_deg")),
            float(match.group("lon_min")),
            float(match.group("lon_sec")),
            match.group("lon_dir"),
        )
        if _valid_coordinate_pair(latitude, longitude):
            candidates.coordinates.append(
                CoordinatePairCandidate(
                    latitude=latitude,
                    longitude=longitude,
                    format="dms",
                    evidence_text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
            )

    for match in _DATE_PATTERN.finditer(text):
        candidates.dates.append(
            TextCandidate(
                value=match.group(0),
                evidence_text=match.group(0),
                start=match.start(),
                end=match.end(),
            )
        )

    for match in _REFERENCE_PATTERN.finditer(text):
        candidates.reference_numbers.append(
            TextCandidate(
                value=match.group("value"),
                evidence_text=match.group(0),
                start=match.start(),
                end=match.end(),
            )
        )

    for match in _LOCATION_PATTERN.finditer(text):
        candidates.locations.append(
            LocationCandidate(
                kind=match.group("kind").lower(),
                value=match.group("value").strip(),
                evidence_text=match.group(0),
                start=match.start(),
                end=match.end(),
            )
        )

    return candidates
