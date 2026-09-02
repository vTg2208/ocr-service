"""Public request contract for claim-level historical evidence."""

from datetime import datetime, timezone

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


__all__ = ["HistoricalEvidenceRequest"]
