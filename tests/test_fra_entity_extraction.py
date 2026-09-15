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

    def test_extracts_fra_entities_across_pages_with_confidence_and_page_evidence(self):
        result = self.extractor.extract(
            "TN-FRA-2025-1",
            {
                "ocr_confidence": 0.9,
                "pages": [
                    {
                        "page_number": 1,
                        "confidence": 0.92,
                        "text": (
                            "Claim Number: TN/FRA/2025/1\nClaimant Name: Ramu\n"
                            "Household Name: Ramu family\nDistrict: Salem\nBlock: Yercaud\n"
                            "Village: Nagalur\nSurvey Number: 614\nSubdivision Number: 1B\n"
                            "Claim Type: IFR\nClaimed Area: 1.25 hectares\nClaim Status: Pending"
                        ),
                    },
                    {
                        "page_number": 2,
                        "confidence": 0.88,
                        "text": (
                        "Gram Sabha Status: verified\nSDLC Status: recommended\n"
                            "DLC Status: granted\nDecision Authority: District Level Committee\n"
                            "Decision Date: 14/11/2025\n"
                            "FRA Title Number: TN-ROFR-44\nGranted Area: 1.10 hectares\n"
                            "Coordinates: 11.783, 78.209"
                        ),
                    },
                ],
            },
        )

        self.assertEqual(result.fields["survey_number"], "614")
        self.assertEqual(result.fields["subdivision_number"], "1B")
        self.assertEqual(result.fields["decision_date"], "2025-11-14")
        self.assertEqual(result.fields["decision_authority"], "District Level Committee")
        self.assertEqual(result.fields["title_number"], "TN-ROFR-44")
        self.assertEqual(result.fields["claimed_area_sqm"], 12500.0)
        self.assertEqual(result.fields["granted_area_sqm"], 11000.0)
        self.assertEqual(result.fields["latitude"], 11.783)
        self.assertEqual(result.fields["longitude"], 78.209)
        self.assertEqual(result.field_evidence["title_number"]["source_page"], 2)
        self.assertEqual(result.field_evidence["title_number"]["extraction_method"], "fra_label_ner")
        self.assertGreater(result.field_evidence["title_number"]["confidence"], 0.7)
        self.assertEqual(result.field_evidence["claimed_area_sqm"]["extraction_method"], "area_unit_parser")
        self.assertEqual(result.provenance["ocr_pages"][1]["line_count"], 8)
        self.assertGreater(result.confidence, 0.7)

    def test_conflicting_values_are_flagged_for_review_instead_of_selected(self):
        result = self.extractor.extract(
            "ambiguous-record",
            {
                "pages": [
                    {"page_number": 1, "confidence": 0.8, "text": "Village: Kottur"},
                    {"page_number": 2, "confidence": 0.8, "text": "Village: Nagalur"},
                ]
            },
        )

        self.assertIsNone(result.fields["village"])
        self.assertTrue(result.field_evidence["village"]["ambiguous"])
        self.assertEqual(len(result.field_evidence["village"]["candidates"]), 2)
        self.assertTrue(any("Ambiguous village" in warning for warning in result.warnings))

    def test_tamil_fra_layout_extracts_land_identifiers_and_fra_title(self):
        result = self.extractor.extract_text(
            "மாவட்டம் : விழுப்புரம்\nவட்டம் : விழுப்புரம்\n"
            "வருவாய் கிராமம் : அற்பிசம்பாளையம்\nவன உரிமை ஆவண எண் : TN-ROFR-2151\n"
            "புல எண் : 614\nஉட்பிரிவு : 1B\nஉரிமையாளர்கள் பெயர் : சர்வே"
        )

        self.assertEqual(result.fields["district"], "விழுப்புரம்")
        self.assertEqual(result.fields["block"], "விழுப்புரம்")
        self.assertEqual(result.fields["village"], "அற்பிசம்பாளையம்")
        self.assertEqual(result.fields["survey_number"], "614")
        self.assertEqual(result.fields["subdivision_number"], "1B")
        self.assertEqual(result.fields["title_number"], "TN-ROFR-2151")
        self.assertNotIn("cadastral_patta_number", result.fields)

    def test_invalid_typed_values_are_left_unset_with_validation_warnings(self):
        result = self.extractor.extract_text(
            "Claim Type: maybe\nDecision Date: 45/19/2025\nCoordinates: 140, 250"
        )

        self.assertIsNone(result.fields["right_type"])
        self.assertIsNone(result.fields["decision_date"])
        self.assertIsNone(result.fields["coordinates"])
        self.assertTrue(any("Invalid right_type" in warning for warning in result.warnings))
        self.assertTrue(any("Invalid decision_date" in warning for warning in result.warnings))
        self.assertTrue(any("Invalid coordinates" in warning for warning in result.warnings))

    def test_layout_ner_associates_separate_table_labels_and_values(self):
        result = self.extractor.extract(
            "structured-page",
            {
                "pages": [{
                    "page_number": 1,
                    "confidence": 0.9,
                    "text": "வருவாய் கிராமம்\nஅற்பிசம்பாளையம்\nபுல எண்\nஉட்பிரிவு\n614\n1B",
                    "lines": [
                        {"text": "வருவாய் கிராமம்", "confidence": 0.94, "bounding_box": [10, 40, 105, 62]},
                        {"text": "அற்பிசம்பாளையம்", "confidence": 0.90, "bounding_box": [125, 40, 250, 62]},
                        {"text": "புல எண்", "confidence": 0.93, "bounding_box": [10, 90, 90, 112]},
                        {"text": "உட்பிரிவு", "confidence": 0.92, "bounding_box": [110, 90, 200, 112]},
                        {"text": "614", "confidence": 0.96, "bounding_box": [20, 210, 65, 232]},
                        {"text": "1B", "confidence": 0.95, "bounding_box": [125, 210, 165, 232]},
                    ],
                }],
            },
        )

        self.assertEqual(result.fields["village"], "அற்பிசம்பாளையம்")
        self.assertEqual(result.fields["survey_number"], "614")
        self.assertEqual(result.fields["subdivision_number"], "1B")
        self.assertEqual(result.field_evidence["survey_number"]["extraction_method"], "fra_layout_ner")
        self.assertEqual(result.field_evidence["survey_number"]["source_page"], 1)

    def test_numbered_form_a_layout_uses_boxes_and_keeps_multiple_parcels_ambiguous(self):
        def line(text, box):
            return {"text": text, "confidence": 0.95, "bounding_box": box}

        lines = [
            line("Form - A", [900, 0, 1000, 25]),
            line("CLAIM FORM FOR INDMDUAL FOREST RIGHI", [300, 40, 750, 70]),
            line("1. Name of the daimant", [100, 100, 320, 130]),
            line(":", [510, 105, 525, 120]),
            line("M. Ramu", [540, 100, 700, 130]),
            line("7. Name of Vitlage", [100, 200, 300, 230]),
            line("Kottur", [540, 200, 650, 230]),
            line("Takuk", [100, 250, 210, 280]),
            line("Yercaud", [540, 250, 650, 280]),
            line("10. District", [100, 300, 230, 330]),
            line("Salem", [540, 300, 650, 330]),
            line("Survey No.", [210, 400, 320, 430]),
            line("178/1", [220, 450, 290, 480]),
            line("178/2", [220, 500, 290, 530]),
            line("Total extent caimed", [100, 600, 350, 630]),
            line("2.10 hectares", [410, 600, 590, 630]),
            line("√", [560, 700, 590, 730]),
            line("Individual Forest Right (IFR)", [610, 700, 900, 730]),
            line("Community Right (CR)", [610, 740, 850, 770]),
            line("Community Forest Resource Right (CFR)", [610, 780, 950, 810]),
        ]
        result = self.extractor.extract("form-a-scan", {
            "intake_kind": "new_claim",
            "pages": [{"page_number": 1, "text": "\n".join(item["text"] for item in lines),
                       "confidence": 0.95, "lines": lines}],
        })
        self.assertEqual(result.fields["holder_name"], "M. Ramu")
        self.assertEqual(result.fields["village"], "Kottur")
        self.assertEqual(result.fields["block"], "Yercaud")
        self.assertEqual(result.fields["district"], "Salem")
        self.assertEqual(result.fields["right_type"], "IFR")
        self.assertEqual(result.fields["claimed_area_sqm"], 21000)
        self.assertIsNone(result.fields["survey_number"])
        self.assertTrue(result.field_evidence["survey_number"]["ambiguous"])
        self.assertEqual(result.field_evidence["holder_name"]["source_page"], 1)
        self.assertEqual(result.field_evidence["holder_name"]["source_bounding_box"], [540, 100, 700, 130])

    def test_form_a_option_list_without_checkmark_does_not_invent_right_type(self):
        lines = [
            {"text": "Form - A", "confidence": 0.9, "bounding_box": [10, 10, 80, 30]},
            {"text": "Individual Forest Right (IFR)", "confidence": 0.9, "bounding_box": [100, 80, 350, 100]},
            {"text": "Community Right (CR)", "confidence": 0.9, "bounding_box": [100, 110, 300, 130]},
        ]
        result = self.extractor.extract("unmarked-form-a", {
            "intake_kind": "new_claim",
            "pages": [{"page_number": 1, "text": "\n".join(item["text"] for item in lines),
                       "confidence": 0.9, "lines": lines}],
        })
        self.assertIsNone(result.fields.get("right_type"))
        self.assertIn("Missing right_type", result.warnings)


if __name__ == "__main__":
    unittest.main()
