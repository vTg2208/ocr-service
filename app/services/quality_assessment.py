"""Deterministic review signals for OCR output.

These checks identify fields that deserve source verification. They do
not claim to validate or correct the OCR result.
"""

import re
from typing import List

from app.models.response_models import OCRQualityAssessment, SurveyFieldCandidate


_DATE_PATTERN = re.compile(r"(?<!\d)\d{2}\.\d{2}\.\d{4}(?!\d)")
_AREA_PATTERN = re.compile(r"(?<!\d)\d{1,2}\.\d{2}\.\d{1,2}(?!\d)")
_SURVEY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<identifier>\d{1,4}/[A-Za-z0-9]+)"
    r"\s*[([]\s*(?P<value>\d{1,2}\.\d{2}\.\d{1,2})\s*[)\]]"
)
_TOKEN_PATTERN = re.compile(r"[^\s,;:()\[\]{}]+")
_TAMIL_PATTERN = re.compile(r"[\u0B80-\u0BFF]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")


def _unique(values: List[str]) -> List[str]:
    return list(dict.fromkeys(values))


def assess_ocr_quality(text: str, model_confidence: float) -> OCRQualityAssessment:
    """Build review signals without treating model confidence as accuracy."""
    dates = _unique(_DATE_PATTERN.findall(text))
    area_values = _unique(_AREA_PATTERN.findall(text))
    survey_fields = [
        SurveyFieldCandidate(
            identifier=match.group("identifier"),
            value=match.group("value"),
            source_verified=False,
        )
        for match in _SURVEY_PATTERN.finditer(text)
    ]
    mixed_script_tokens = _unique(
        [
            token.strip(".-/\"")
            for token in _TOKEN_PATTERN.findall(text)
            if _TAMIL_PATTERN.search(token) and _LATIN_PATTERN.search(token)
        ]
    )

    review_reasons = []
    if survey_fields or area_values:
        review_reasons.append(
            "Critical numeric or survey fields were detected and are not source-verified."
        )
    if dates:
        review_reasons.append("Dates were detected and are not source-verified.")
    if mixed_script_tokens:
        review_reasons.append(
            "Mixed Tamil/Latin tokens may indicate character-recognition corruption."
        )
    if model_confidence < 90:
        review_reasons.append("Model confidence is below 90; inspect the source document.")

    return OCRQualityAssessment(
        model_confidence=model_confidence,
        confidence_is_text_accuracy=False,
        requires_human_review=bool(review_reasons),
        review_reasons=review_reasons,
        dates=dates,
        area_values=area_values,
        survey_fields=survey_fields,
        mixed_script_tokens=mixed_script_tokens,
    )
