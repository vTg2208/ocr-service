from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ParcelFields(BaseModel):
    state: str
    district: str
    taluk: str
    village: str
    survey_number: str
    subdivision_number: str = ""
    document_area_sqm: float | None = Field(default=None, ge=0)


class ResolveRequest(ParcelFields):
    document_id: UUID


class ClaimRequest(BaseModel):
    document_id: UUID
    parcel_id: UUID
    confirmed_fields: dict[str, Any]


class ConflictUpdate(BaseModel):
    status: Literal["open", "reviewing", "resolved", "dismissed"]
    resolution_notes: str | None = None
