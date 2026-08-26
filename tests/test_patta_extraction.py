import unittest

from app.services.patta_extraction import extract_normalized_parcel_fields


class PattaExtractionTests(unittest.TestCase):
    def test_extracts_complete_lookup_key_with_evidence_and_area(self):
        result = extract_normalized_parcel_fields(
            "State: Tamil Nadu\nDistrict: Thanjavur\nTaluk: Kumbakonam\n"
            "Village: Example Village\nSurvey No. 701 / 4 b\nExtent: 0.12 hectares",
            ocr_confidence=91,
        )
        self.assertEqual(result["survey_number"], "701")
        self.assertEqual(result["subdivision_number"], "4B")
        self.assertEqual(result["document_area_sqm"], 1200)
        self.assertEqual(result["evidence"]["survey_number"], "Survey No. 701 / 4 b")
        self.assertEqual(result["confidence"], 0.91)

    def test_does_not_treat_numeric_area_as_survey_reference(self):
        result = extract_normalized_parcel_fields("Extent: 0.42 hectares", 80)
        self.assertIsNone(result["survey_number"])
        self.assertEqual(result["document_area_sqm"], 4200)

    def test_converts_hectare_are_square_metre_notation(self):
        result = extract_normalized_parcel_fields("Extent: 0.12.00 hectares", 90)
        self.assertEqual(result["document_area_sqm"], 1200)
        self.assertEqual(result["original_area"]["value"], "0.12.00")

    def test_extracts_lookup_key_from_tamil_patta_table_layout(self):
        result = extract_normalized_parcel_fields(
            """தமிழ்நாடு அரசு
மாவட்டம் : விழுப்புரம்
வட்டம் : விழுப்புரம்
வருவாய் கிராமம் : அற்பிசம்பாளையம்
பட்டா எண் : 2151
புல எண்
உட்பிரிவு
பரப்பு
நீர்வை
614
1B
0 - 5.00
0.21""",
            ocr_confidence=97.86,
        )

        self.assertEqual(result["state"], "தமிழ்நாடு")
        self.assertEqual(result["district"], "விழுப்புரம்")
        self.assertEqual(result["taluk"], "விழுப்புரம்")
        self.assertEqual(result["village"], "அற்பிசம்பாளையம்")
        self.assertEqual(result["survey_number"], "614")
        self.assertEqual(result["subdivision_number"], "1B")
        self.assertEqual(result["document_area_sqm"], 500)
        self.assertEqual(result["original_area"], {"value": "0 - 5.00", "unit": "hectare-are"})


if __name__ == "__main__":
    unittest.main()
