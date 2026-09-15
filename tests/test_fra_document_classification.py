import unittest

from app.services.fra_document_classification import classify_fra_document


class FRADocumentClassificationTests(unittest.TestCase):
    def test_document_type_is_separate_from_fra_right_type(self):
        examples = {
            "FRA Claim Form\nRight Type: CFR": "claim_form",
            "CLAIM FORM FOR INDMDUAL FOREST RIGHI": "claim_form",
            "Gram Sabha Resolution for CFR claim": "gram_sabha_record",
            "SDLC Decision on IFR application": "sdlc_record",
            "DLC Proceedings on CR application": "dlc_record",
            "Record of Forest Rights, title 14": "title_right_record",
            "Forest right sketch of land": "map_sketch",
        }
        for text, expected in examples.items():
            with self.subTest(expected=expected):
                result = classify_fra_document([{"page_number": 2, "text": text}])
                self.assertEqual(result["document_type"], expected)
                self.assertEqual(result["source_page"], 2)

    def test_unreadable_or_mixed_document_type_requires_review(self):
        self.assertEqual(classify_fra_document([{"page_number": 1, "text": ""}])["document_type"], "unknown")
        mixed = classify_fra_document([
            {"page_number": 1, "text": "FRA Claim Form"},
            {"page_number": 2, "text": "Gram Sabha Resolution"},
        ])
        self.assertTrue(mixed["ambiguous"])
        self.assertEqual(mixed["document_type"], "unknown")
