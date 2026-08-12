"""Ground-truth OCR evaluation metrics."""

from collections import Counter
import re
import unicodedata
from typing import Sequence

from app.models.response_models import OCREvaluationResponse


_DATE_PATTERN = re.compile(r"(?<!\d)\d{2}\.\d{2}\.\d{4}(?!\d)")
_NUMERIC_FIELD_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])\d+(?:[./-][A-Za-z0-9]+)+(?![A-Za-z0-9])"
)
_SURVEY_PATTERN = re.compile(r"(?<![A-Za-z0-9])\d{1,4}/[A-Za-z0-9]+")
_CRITICAL_PAIR_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<identifier>\d{1,4}/[A-Za-z0-9]+)"
    r"\s*[([]\s*(?P<value>\d{1,2}\.\d{2}\.\d{1,2})\s*[)\]]"
)


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def _edit_distance(expected: Sequence, actual: Sequence) -> int:
    if len(expected) < len(actual):
        expected, actual = actual, expected

    previous = list(range(len(actual) + 1))
    for row, expected_item in enumerate(expected, start=1):
        current = [row]
        for column, actual_item in enumerate(actual, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (expected_item != actual_item),
                )
            )
        previous = current
    return previous[-1]


def _field_accuracy(expected: list[str], actual: list[str]) -> float:
    if not expected:
        return 100.0 if not actual else 0.0
    matches = sum((Counter(expected) & Counter(actual)).values())
    return round(matches / len(expected) * 100, 2)


def _critical_pairs(text: str) -> list[str]:
    return [
        f"{match.group('identifier')}|{match.group('value')}"
        for match in _CRITICAL_PAIR_PATTERN.finditer(text)
    ]


def evaluate_ocr_text(reference_text: str, ocr_text: str) -> OCREvaluationResponse:
    """Compare OCR output with human-verified reference text."""
    reference = _normalize(reference_text)
    prediction = _normalize(ocr_text)
    if not reference:
        raise ValueError("Reference text must not be empty.")

    reference_words = reference.split()
    prediction_words = prediction.split()

    return OCREvaluationResponse(
        character_error_rate=round(_edit_distance(reference, prediction) / len(reference), 4),
        word_error_rate=round(
            _edit_distance(reference_words, prediction_words) / len(reference_words),
            4,
        ),
        numeric_field_accuracy=_field_accuracy(
            _NUMERIC_FIELD_PATTERN.findall(reference),
            _NUMERIC_FIELD_PATTERN.findall(prediction),
        ),
        date_accuracy=_field_accuracy(
            _DATE_PATTERN.findall(reference),
            _DATE_PATTERN.findall(prediction),
        ),
        survey_number_accuracy=_field_accuracy(
            _SURVEY_PATTERN.findall(reference),
            _SURVEY_PATTERN.findall(prediction),
        ),
        critical_field_exact_match_accuracy=_field_accuracy(
            _critical_pairs(reference),
            _critical_pairs(prediction),
        ),
    )
