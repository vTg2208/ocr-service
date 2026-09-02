"""Validated contracts for reviewer-controlled legacy-to-FRA intake."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.fra_schemas import RightType


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


IntakeReviewState = Literal[
    "awaiting_triage", "ready_for_promotion", "not_fra", "duplicate"
]


class FRAIntakeUpdate(StrictModel):
    target_state: IntakeReviewState
    expected_revision: int = Field(ge=0)
    reasons: list[str] = Field(default_factory=list, max_length=20)
    triage: dict[str, Any] = Field(default_factory=dict)


class FRAIntakePromote(StrictModel):
    rights_holder_id: UUID
    right_type: RightType
    gram_sabha_id: UUID | None = None
    expected_revision: int = Field(ge=0)
