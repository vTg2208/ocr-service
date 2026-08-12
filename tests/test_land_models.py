import unittest

from app.models.response_models import (
    AreaField,
    EvidencedText,
    FieldEvidence,
    LandExtractionResult,
    LandRecord,
)


class LandModelTests(unittest.TestCase):
    def test_land_record_serializes_evidence_and_missing_coordinates(self):
        evidence = FieldEvidence(
            text="207/9 (0.04.00)",
            method="deterministic",
            confidence=1.0,
        )
        record = LandRecord(
            record_id="land-1",
            survey_number=EvidencedText(value="207/9", evidence=evidence),
            area=AreaField(
                raw_value="0.04.00",
                unit=None,
                normalized_square_metres=None,
                evidence=evidence,
            ),
        )
        result = LandExtractionResult(
            status="partial",
            records=[record],
            record_count=1,
            requires_human_review=True,
            warnings=["Source verification required."],
        )

        payload = result.model_dump()
        self.assertIsNone(payload["records"][0]["latitude"])
        self.assertFalse(
            payload["records"][0]["survey_number"]["evidence"]["source_verified"]
        )


if __name__ == "__main__":
    unittest.main()
