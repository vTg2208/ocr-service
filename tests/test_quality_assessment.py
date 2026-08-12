import unittest

from app.services.quality_assessment import assess_ocr_quality


class OCRQualityAssessmentTests(unittest.TestCase):
    def test_flags_critical_fields_and_mixed_script_artifacts(self):
        text = (
            "207/9 (0.01.00) 208/281 (0.08.50) 03.02.2024 "
            "\u0b8eabr\u0b95dr APK Minerals Pvt.Ltd"
        )

        quality = assess_ocr_quality(text, model_confidence=95.06)

        self.assertTrue(quality.requires_human_review)
        self.assertEqual(quality.dates, ["03.02.2024"])
        self.assertEqual(
            [(field.identifier, field.value) for field in quality.survey_fields],
            [("207/9", "0.01.00"), ("208/281", "0.08.50")],
        )
        self.assertEqual(quality.mixed_script_tokens, ["\u0b8eabr\u0b95dr"])
        self.assertIn(
            "Critical numeric or survey fields were detected and are not source-verified.",
            quality.review_reasons,
        )
        self.assertFalse(quality.confidence_is_text_accuracy)

    def test_clean_noncritical_text_does_not_force_review(self):
        quality = assess_ocr_quality("Plain OCR text", model_confidence=96.0)

        self.assertFalse(quality.requires_human_review)
        self.assertEqual(quality.review_reasons, [])


if __name__ == "__main__":
    unittest.main()
