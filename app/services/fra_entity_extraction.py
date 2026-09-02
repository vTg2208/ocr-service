"""Tamil Nadu FRA field normalization with line-level source evidence."""

import re
import time

from app.services.model_gateway import (
    EntityExtractionResult,
    ModelOutputValidationError,
    validate_model_output,
)
from app.services.state_profiles import get_state_profile


FIELD_LABELS = {
    "claim_number": ("Claim No", "Claim Number", "கோரிக்கை எண்", "விண்ணப்ப எண்"),
    "holder_name": ("Claimant", "Rights Holder", "Holder Name", "கோரிக்கையாளர்", "உரிமையாளர் பெயர்"),
    "district": ("District", "மாவட்டம்"),
    "block": ("Block", "Taluk", "வட்டம்", "ஒன்றியம்"),
    "village": ("Village", "கிராமம்"),
    "right_type": ("Right Type", "Rights Type", "உரிமை வகை"),
    "claim_status": ("Claim Status", "Status", "நிலை"),
    "claim_year": ("Claim Year", "Year", "கோரிக்கை ஆண்டு", "ஆண்டு"),
}
REQUIRED_REVIEW_FIELDS = (
    "claim_number",
    "holder_name",
    "district",
    "block",
    "village",
    "right_type",
    "claim_status",
)


def _label_pattern(labels: tuple[str, ...]) -> re.Pattern:
    choices = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    return re.compile(rf"^\s*(?:{choices})\s*[:：-]\s*(?P<value>.+?)\s*$", re.IGNORECASE)


PATTERNS = {field: _label_pattern(labels) for field, labels in FIELD_LABELS.items()}


class TamilNaduFRAExtractor:
    """Allow-listed local extractor for Tamil and English FRA record text."""

    def __init__(self, version: str):
        self.version = version.strip()
        if not self.version:
            raise ModelOutputValidationError("A model version is required.")

    def extract(self, document_reference: str, manifest: dict) -> EntityExtractionResult:
        if not document_reference.strip():
            raise ModelOutputValidationError("A document reference is required.")
        if not isinstance(manifest, dict):
            raise ModelOutputValidationError("Extraction input must be an object.")
        raw_text = manifest.get("raw_text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ModelOutputValidationError("OCR text is required for FRA entity extraction.")
        return self.extract_text(raw_text, document_reference=document_reference)

    def extract_text(
        self,
        raw_text: str,
        *,
        document_reference: str = "in-memory-text",
    ) -> EntityExtractionResult:
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ModelOutputValidationError("OCR text is required for FRA entity extraction.")
        started = time.perf_counter()
        fields = {}
        evidence = {}
        for line_number, line in enumerate(raw_text.splitlines(), start=1):
            clean_line = line.strip()
            if not clean_line:
                continue
            for field, pattern in PATTERNS.items():
                if field in fields:
                    continue
                match = pattern.match(clean_line)
                if match is None:
                    continue
                value = " ".join(match.group("value").split())
                if value:
                    fields[field] = value
                    evidence[field] = {"text": clean_line, "line": line_number}
                break

        profile = get_state_profile("TN")
        fields["state"] = profile.name
        fields["state_code"] = profile.code
        for field, normalizer in (
            ("district", profile.normalize_district),
            ("block", profile.normalize_block),
            ("village", profile.normalize_village),
        ):
            if field in fields:
                fields[field] = normalizer(fields[field])
        if "right_type" in fields:
            fields["right_type"] = fields["right_type"].upper()
        if "claim_status" in fields:
            fields["claim_status"] = fields["claim_status"].casefold()
        if "claim_year" in fields:
            year = fields["claim_year"]
            fields["claim_year"] = int(year) if re.fullmatch(r"\d{4}", year) else year

        validate_model_output(fields)
        missing = [field for field in REQUIRED_REVIEW_FIELDS if not fields.get(field)]
        warnings = [f"Missing {field}" for field in missing]
        elapsed = max(0, round((time.perf_counter() - started) * 1000))
        return EntityExtractionResult(
            fields=fields,
            field_evidence=evidence,
            confidence=None,
            model_version=self.version,
            processing_time_ms=elapsed,
            provenance={
                "adapter": "local_python",
                "runner": "tamil_nadu_fra_regex_v1",
                "state_code": "TN",
                "document_reference": document_reference,
                "missing_fields": missing,
                "legal_role": "unverified_extraction",
            },
            warnings=warnings,
        )


__all__ = ["TamilNaduFRAExtractor"]
