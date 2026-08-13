import unittest

from app.services.parcel_normalization import (
    area_difference_percent,
    calculate_match_confidence,
    convert_area_to_sqm,
    normalize_admin_name,
    parse_parcel_reference,
)


class ParcelReferenceTests(unittest.TestCase):
    def test_splits_equivalent_survey_subdivision_forms(self):
        for raw in ("701/4b", "701 / 4 B", "701-4B"):
            with self.subTest(raw=raw):
                parsed = parse_parcel_reference(raw)
                self.assertEqual(parsed.survey_number, "701")
                self.assertEqual(parsed.subdivision_number, "4B")

    def test_preserves_meaningful_leading_zeros(self):
        parsed = parse_parcel_reference("007/04a")
        self.assertEqual((parsed.survey_number, parsed.subdivision_number), ("007", "04A"))

    def test_reports_ambiguous_ocr_alternatives_without_changing_value(self):
        parsed = parse_parcel_reference("701/4B")
        self.assertEqual(parsed.subdivision_number, "4B")
        self.assertIn("48", parsed.alternatives)
        self.assertTrue(parsed.needs_confirmation)

    def test_rejects_area_as_parcel_reference(self):
        with self.assertRaises(ValueError):
            parse_parcel_reference("0.42 hectares")


class AreaNormalizationTests(unittest.TestCase):
    def test_converts_all_supported_units(self):
        cases = [
            (1200, "square metres", 1200.0),
            (0.42, "hectares", 4200.0),
            (1, "acre", 4046.8564224),
            (1, "cent", 40.468564224),
        ]
        for value, unit, expected in cases:
            with self.subTest(unit=unit):
                self.assertAlmostEqual(convert_area_to_sqm(value, unit), expected)

    def test_calculates_area_difference_against_registry_area(self):
        self.assertAlmostEqual(area_difference_percent(1200, 1180), 1.6949152542)
        self.assertIsNone(area_difference_percent(None, 1180))


class AdministrativeAndConfidenceTests(unittest.TestCase):
    def test_normalizes_case_whitespace_and_verified_alias(self):
        aliases = {"thanjavoor": "Thanjavur"}
        self.assertEqual(normalize_admin_name("  THANJAVOOR  ", aliases), "Thanjavur")
        self.assertEqual(normalize_admin_name(" example   village "), "Example Village")

    def test_confidence_is_explainable_and_penalizes_area_mismatch(self):
        exact = calculate_match_confidence(
            exact_fields=6, total_fields=6, ocr_confidence=0.9, area_difference=2, area_tolerance=10
        )
        mismatch = calculate_match_confidence(
            exact_fields=6, total_fields=6, ocr_confidence=0.9, area_difference=30, area_tolerance=10
        )
        self.assertEqual(exact.score, 0.98)
        self.assertGreater(exact.score, mismatch.score)
        self.assertIn("area_within_tolerance", exact.reasons)
        self.assertIn("area_outside_tolerance", mismatch.reasons)


if __name__ == "__main__":
    unittest.main()
