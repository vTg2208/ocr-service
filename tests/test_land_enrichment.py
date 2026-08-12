import json
from types import SimpleNamespace
import unittest

from app.services.land_enrichment import (
    LandEnrichmentService,
    build_deterministic_land_result,
)


def fake_llm_payload():
    return {
        "records": [
            {
                "survey_number": "207/9",
                "holder": {
                    "value": "APK Minerals Pvt.Ltd",
                    "evidence_text": "APK Minerals Pvt.Ltd",
                    "confidence": 0.94,
                },
                "location": {
                    "district": {
                        "value": "Kanchipuram",
                        "evidence_text": "Kanchipuram District",
                        "confidence": 0.9,
                    }
                },
            }
        ]
    }


class FakeCompletions:
    def __init__(self, payload):
        self.payload = payload

    async def create(self, **kwargs):
        message = SimpleNamespace(content=json.dumps(self.payload))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, payload):
        self.chat = SimpleNamespace(completions=FakeCompletions(payload))


class DeterministicLandEnrichmentTests(unittest.TestCase):
    def test_builds_one_unverified_record_per_parcel(self):
        result, _ = build_deterministic_land_result(
            "207/9 (0.04.00), 208/2B1 (0.08.50)"
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.record_count, 2)
        self.assertEqual(
            [record.record_id for record in result.records],
            ["land-1", "land-2"],
        )
        self.assertEqual(
            [record.survey_number.value for record in result.records],
            ["207/9", "208/2B1"],
        )
        self.assertTrue(
            all(not record.area.evidence.source_verified for record in result.records)
        )
        self.assertTrue(result.requires_human_review)

    def test_repeats_labeled_shared_location_and_document_references(self):
        result, _ = build_deterministic_land_result(
            "Pazhaveri Village, Kanchipuram District, Ref No.346/Q3/2022 "
            "dated 03.02.2024: 207/9 (0.04.00), 208/2B1 (0.08.50)"
        )
        self.assertEqual(
            [record.location.village.value for record in result.records],
            ["Pazhaveri", "Pazhaveri"],
        )
        self.assertEqual(
            [record.location.district.value for record in result.records],
            ["Kanchipuram", "Kanchipuram"],
        )
        self.assertEqual(
            [item.value for item in result.records[0].document_references],
            ["03.02.2024", "346/Q3/2022"],
        )


class CoordinateAssociationTests(unittest.TestCase):
    def test_attaches_one_explicit_coordinate_pair_only_to_single_parcel(self):
        result, _ = build_deterministic_land_result(
            "207/9 (0.04.00) Latitude: 12.6934 Longitude: 79.9757"
        )
        self.assertEqual(result.records[0].latitude.value, 12.6934)
        self.assertEqual(result.records[0].longitude.value, 79.9757)

        ambiguous, _ = build_deterministic_land_result(
            "207/9 (0.04.00), 208/2B1 (0.08.50) "
            "Latitude: 12.6934 Longitude: 79.9757"
        )
        self.assertTrue(all(record.latitude is None for record in ambiguous.records))
        self.assertTrue(
            any("coordinate" in warning.lower() for warning in ambiguous.warnings)
        )


class LandEnrichmentFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_llm_returns_deterministic_not_configured_result(self):
        service = LandEnrichmentService(client=None, model_name="unused")
        result = await service.extract("207/9 (0.04.00)")
        self.assertEqual(result.status, "not_configured")
        self.assertIsNone(result.records[0].holder)
        self.assertEqual(result.records[0].survey_number.value, "207/9")


class LandEvidenceValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_hallucinated_holder_and_numeric_values(self):
        text = "207/9 (0.04.00), Kanchipuram District"
        payload = fake_llm_payload()
        payload["records"][0]["holder"] = {
            "value": "Invented Owner",
            "evidence_text": "Invented Owner",
            "confidence": 0.99,
        }
        payload["records"][0]["area"] = {
            "raw_value": "9.99.99",
            "evidence_text": "207/9 (0.04.00)",
            "confidence": 0.99,
        }
        service = LandEnrichmentService(
            client=FakeClient(payload),
            model_name="test-model",
        )
        result = await service.extract(text)
        self.assertIsNone(result.records[0].holder)
        self.assertEqual(result.records[0].area.raw_value, "0.04.00")
        self.assertTrue(
            any("rejected" in warning.lower() for warning in result.warnings)
        )


class SharedHolderGroupingTests(unittest.IsolatedAsyncioTestCase):
    async def test_adds_supported_holder_to_each_llm_grouped_parcel(self):
        text = (
            "APK Minerals Pvt.Ltd applied for "
            "207/9 (0.04.00), 208/2B1 (0.08.50)"
        )
        payload = {
            "records": [
                {
                    "survey_number": "207/9",
                    "holder": {
                        "value": "APK Minerals Pvt.Ltd",
                        "evidence_text": "APK Minerals Pvt.Ltd",
                        "confidence": 0.94,
                    },
                },
                {
                    "survey_number": "208/2B1",
                    "holder": {
                        "value": "APK Minerals Pvt.Ltd",
                        "evidence_text": "APK Minerals Pvt.Ltd",
                        "confidence": 0.94,
                    },
                },
            ]
        }
        service = LandEnrichmentService(
            client=FakeClient(payload),
            model_name="test-model",
        )
        result = await service.extract(text)
        self.assertEqual(
            [record.holder.value for record in result.records],
            ["APK Minerals Pvt.Ltd", "APK Minerals Pvt.Ltd"],
        )
        self.assertTrue(
            all(
                record.holder.evidence.method == "llm_with_ocr_evidence"
                for record in result.records
            )
        )


if __name__ == "__main__":
    unittest.main()
