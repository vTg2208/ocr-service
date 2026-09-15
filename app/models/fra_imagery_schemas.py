"""Public request contract for claim-level historical evidence."""

from datetime import date, datetime, timezone

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HistoricalEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_years: list[int] = Field(min_length=1, max_length=10)

    @field_validator("target_years")
    @classmethod
    def validate_years(cls, years: list[int]) -> list[int]:
        current_year = datetime.now(timezone.utc).year
        if any(isinstance(year, bool) or year < 1972 or year > current_year for year in years):
            raise ValueError(f"Target years must be between 1972 and {current_year}.")
        return sorted(set(years))


class HistoricalEvidenceReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_reviewed_at: datetime | None
    verification_state: Literal["verified", "rejected", "needs_field_verification"]
    notes: str = Field(min_length=1, max_length=2000)


class SatelliteIngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    collection: str = Field(min_length=1, max_length=100)
    band_keys: list[str] = Field(min_length=1, max_length=6)
    max_cloud: float = Field(default=30, ge=0, le=100)

    @field_validator("band_keys")
    @classmethod
    def validate_band_keys(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if len(set(normalized)) != len(normalized) or any(not value for value in normalized):
            raise ValueError("Satellite band keys must be distinct and non-empty.")
        return normalized


__all__ = [
    "HistoricalEvidenceRequest", "HistoricalEvidenceReview", "SatelliteIngestionRequest",
]
