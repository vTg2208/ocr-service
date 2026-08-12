"""
Pydantic models describing API request/response shapes.

Keeping these separate from route handlers means the contract of the API
can be inspected, tested, and reused without pulling in FastAPI-specific
routing code.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str = Field(..., example="healthy")


class SurveyFieldCandidate(BaseModel):
    identifier: str
    value: str
    source_verified: bool = False


class OCRQualityAssessment(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_confidence: float = Field(
        ...,
        description="Average recognition confidence reported by the OCR model; not measured accuracy.",
    )
    confidence_is_text_accuracy: bool = False
    requires_human_review: bool
    review_reasons: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    area_values: list[str] = Field(default_factory=list)
    survey_fields: list[SurveyFieldCandidate] = Field(default_factory=list)
    mixed_script_tokens: list[str] = Field(default_factory=list)


class OCREvaluationResponse(BaseModel):
    character_error_rate: float = Field(..., description="Lower is better; 0 means exact character match.")
    word_error_rate: float = Field(..., description="Lower is better; 0 means exact word match.")
    numeric_field_accuracy: float = Field(..., description="Exact-match recall percentage.")
    date_accuracy: float = Field(..., description="Exact-match recall percentage.")
    survey_number_accuracy: float = Field(..., description="Exact-match recall percentage.")
    critical_field_exact_match_accuracy: float = Field(
        ...,
        description="Exact-match recall for survey identifier and area-value pairs.",
    )


class FieldEvidence(BaseModel):
    text: str
    method: Literal["deterministic", "llm_with_ocr_evidence", "hybrid"]
    confidence: float = Field(..., ge=0, le=1)
    source_verified: bool = False


class EvidencedText(BaseModel):
    value: str
    evidence: FieldEvidence


class AreaField(BaseModel):
    raw_value: str
    unit: str | None = None
    normalized_square_metres: float | None = Field(default=None, ge=0)
    evidence: FieldEvidence


class CoordinateField(BaseModel):
    value: float
    format: Literal["decimal", "dms"]
    evidence: FieldEvidence


class LandLocation(BaseModel):
    village: EvidencedText | None = None
    taluk: EvidencedText | None = None
    district: EvidencedText | None = None
    state: EvidencedText | None = None
    address: EvidencedText | None = None


class DocumentReference(BaseModel):
    kind: str
    value: str
    evidence: FieldEvidence


class OtherLandAttribute(BaseModel):
    key: str
    value: str
    evidence: FieldEvidence


class LandRecord(BaseModel):
    record_id: str
    holder: EvidencedText | None = None
    holder_type: Literal["person", "organization", "unknown"] | None = None
    survey_number: EvidencedText | None = None
    area: AreaField | None = None
    latitude: CoordinateField | None = None
    longitude: CoordinateField | None = None
    location: LandLocation = Field(default_factory=LandLocation)
    land_type: EvidencedText | None = None
    uses_or_resources: list[EvidencedText] = Field(default_factory=list)
    document_references: list[DocumentReference] = Field(default_factory=list)
    other_attributes: list[OtherLandAttribute] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LandExtractionResult(BaseModel):
    status: Literal["completed", "partial", "not_configured", "failed"]
    records: list[LandRecord] = Field(default_factory=list)
    record_count: int = Field(..., ge=0)
    requires_human_review: bool
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class OCRResponse(BaseModel):
    success: bool = True
    filename: str
    processing_time: float = Field(..., description="Time taken in seconds")
    text: str
    confidence: float = Field(
        ...,
        description="Average OCR model confidence (0-100); this is not textual accuracy.",
    )
    quality: OCRQualityAssessment
    prompt_provided: bool = Field(default=False, description="Whether a prompt was provided for LLM analysis")
    analysis: str | None = Field(default=None, description="Dynamic LLM output if prompt was provided")


class OCRLandResponse(BaseModel):
    ocr: OCRResponse
    land_extraction: LandExtractionResult


class ErrorResponse(BaseModel):
    success: bool = False
    message: str
