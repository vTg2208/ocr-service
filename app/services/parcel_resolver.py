"""Deterministic cadastral parcel resolution, independent from OCR."""

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AdministrativeAlias, Parcel
from app.services.parcel_normalization import (
    area_difference_percent,
    calculate_match_confidence,
    normalize_admin_key,
    normalize_admin_name,
    normalize_identifier,
)


@dataclass
class ParcelLookup:
    state: str = ""
    district: str = ""
    taluk: str = ""
    village: str = ""
    survey_number: str = ""
    subdivision_number: str = ""
    document_area_sqm: float | None = None
    ocr_confidence: float = 1.0
    ambiguous_fields: list[str] = field(default_factory=list)


@dataclass
class ResolutionResult:
    status: Literal["matched", "multiple_matches", "not_found", "insufficient_data", "needs_confirmation"]
    parcel: dict | None = None
    match_confidence: float | None = None
    match_method: str | None = None
    area_difference_percent: float | None = None
    warnings: list[str] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    explanations: list[str] = field(default_factory=list)

    def model_dump(self) -> dict:
        return self.__dict__.copy()


def parcel_public_dict(parcel: Parcel) -> dict:
    return {
        "id": str(parcel.id), "state": parcel.state, "district": parcel.district,
        "taluk": parcel.taluk, "village": parcel.village,
        "survey_number": parcel.survey_number,
        "subdivision_number": parcel.subdivision_number,
        "official_area_sqm": float(parcel.official_area_sqm) if parcel.official_area_sqm is not None else None,
        "geometry": parcel.geometry, "source": parcel.source,
        "source_version": parcel.source_version, "boundary_quality": parcel.boundary_quality,
    }


class ParcelResolver:
    LOOKUP_FIELDS = ("state", "district", "taluk", "village", "survey_number", "subdivision_number")

    def __init__(
        self, session: Session, *, area_tolerance_percent: float = 10.0,
        automatic_match_confidence: float = 0.85,
    ):
        self.session = session
        self.area_tolerance_percent = area_tolerance_percent
        self.automatic_match_confidence = automatic_match_confidence

    def _aliases(self) -> dict[tuple[str, str], str]:
        return {
            (row.level, row.normalized_alias): row.canonical_name
            for row in self.session.scalars(select(AdministrativeAlias))
        }

    def _canonical_lookup(self, request: ParcelLookup) -> tuple[dict, list[str]]:
        aliases = self._aliases()
        values = {}
        explanations = []
        for level in ("state", "district", "taluk", "village"):
            raw = getattr(request, level)
            canonical = aliases.get((level, normalize_admin_key(raw)))
            values[level] = canonical or normalize_admin_name(raw)
            if canonical:
                explanations.append(f"verified_alias:{level}")
        values["survey_number"] = normalize_identifier(request.survey_number)
        values["subdivision_number"] = normalize_identifier(request.subdivision_number)
        return values, explanations

    def resolve(self, request: ParcelLookup) -> ResolutionResult:
        missing = [field for field in self.LOOKUP_FIELDS if not str(getattr(request, field, "")).strip()]
        if missing:
            return ResolutionResult(status="insufficient_data", missing_fields=missing)
        values, explanations = self._canonical_lookup(request)
        matches = list(self.session.scalars(select(Parcel).where(
            Parcel.state == values["state"], Parcel.district == values["district"],
            Parcel.taluk == values["taluk"], Parcel.village == values["village"],
            Parcel.survey_number == values["survey_number"],
            Parcel.subdivision_number == values["subdivision_number"],
        )))
        if len(matches) > 1:
            return ResolutionResult(
                status="multiple_matches", alternatives=[parcel_public_dict(item) for item in matches],
                explanations=explanations,
            )
        if not matches:
            suggestions = self._suggest(values)
            return ResolutionResult(
                status=(
                    "multiple_matches" if len(suggestions) > 1
                    else "needs_confirmation" if suggestions
                    else "not_found"
                ),
                alternatives=suggestions, explanations=explanations,
            )
        parcel = matches[0]
        difference = area_difference_percent(
            request.document_area_sqm,
            float(parcel.official_area_sqm) if parcel.official_area_sqm is not None else None,
        )
        confidence = calculate_match_confidence(
            exact_fields=6, total_fields=6, ocr_confidence=request.ocr_confidence,
            area_difference=difference, area_tolerance=self.area_tolerance_percent,
        )
        warnings = []
        if difference is not None and difference > self.area_tolerance_percent:
            warnings.append(
                f"Document area differs from registry area beyond {self.area_tolerance_percent:.1f}%."
            )
        return ResolutionResult(
            status=(
                "needs_confirmation"
                if request.ambiguous_fields or confidence.score < self.automatic_match_confidence
                else "matched"
            ),
            parcel=parcel_public_dict(parcel), match_confidence=confidence.score,
            match_method="exact_composite_key", area_difference_percent=difference,
            warnings=warnings, explanations=explanations + confidence.reasons,
        )

    def _suggest(self, values: dict) -> list[dict]:
        candidates = list(self.session.scalars(select(Parcel).where(
            Parcel.state == values["state"], Parcel.district == values["district"],
            Parcel.taluk == values["taluk"], Parcel.survey_number == values["survey_number"],
            Parcel.subdivision_number == values["subdivision_number"],
        )))
        suggestions = []
        for parcel in candidates:
            if SequenceMatcher(None, parcel.village.casefold(), values["village"].casefold()).ratio() >= 0.75:
                suggestions.append(parcel_public_dict(parcel))
        return suggestions[:5]
