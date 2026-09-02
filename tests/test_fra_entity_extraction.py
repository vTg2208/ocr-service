import unittest

from app.services.fra_entity_extraction import TamilNaduFRAExtractor


class TamilNaduFRAEntityExtractionTests(unittest.TestCase):
    def setUp(self):
        self.extractor = TamilNaduFRAExtractor("tn-fra-regex-v1")

    def test_extracts_english_fields_with_line_level_evidence(self):
        result = self.extractor.extract_text(
            "Claim No: TN/IFR/12\n"
            "Claimant: Ramu\nDistrict: Salem\nBlock: Yercaud\n"
            "Village: Kottur\nRight Type: IFR\nStatus: Pending\nClaim Year: 2024"
        )
        self.assertEqual(result.fields["claim_number"], "TN/IFR/12")
        self.assertEqual(result.fields["holder_name"], "Ramu")
        self.assertEqual(result.fields["village"], "Kottur")
        self.assertEqual(result.fields["claim_status"], "pending")
        self.assertEqual(result.field_evidence["village"]["text"], "Village: Kottur")
        self.assertEqual(result.field_evidence["village"]["line"], 5)
        self.assertEqual(result.provenance["state_code"], "TN")

    def test_extracts_tamil_labels_without_translating_source_values(self):
        result = self.extractor.extract_text(
            "கோரிக்கை எண்: TN/CFR/45\n"
            "கோரிக்கையாளர்: மலை கிராம சபை\n"
            "மாவட்டம்: நீலகிரி\nவட்டம்: கோத்தகிரி\nகிராமம்: ஜக்கனாரை\n"
            "உரிமை வகை: CFR\nநிலை: பரிசீலனையில்"
        )
        self.assertEqual(result.fields["claim_number"], "TN/CFR/45")
        self.assertEqual(result.fields["holder_name"], "மலை கிராம சபை")
        self.assertEqual(result.fields["district"], "நீலகிரி")
        self.assertEqual(result.fields["right_type"], "CFR")
        self.assertEqual(result.fields["claim_status"], "பரிசீலனையில்")

    def test_missing_fields_are_warned_without_an_inferred_legal_result(self):
        result = self.extractor.extract_text("Village: Kottur\nA supporting observation only")
        self.assertEqual(result.fields["village"], "Kottur")
        self.assertNotIn("claim_status", result.fields)
        self.assertNotIn("approved", result.fields)
        self.assertIn("claim_number", result.provenance["missing_fields"])
        self.assertIn("Missing claim_number", result.warnings)


if __name__ == "__main__":
    unittest.main()
