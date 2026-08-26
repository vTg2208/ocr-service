import unittest

from scripts.evaluate_fra_models import evaluation_report


class FRAModelEvaluationTests(unittest.TestCase):
    def test_empty_labels_never_receive_fake_accuracy(self):
        self.assertEqual(
            evaluation_report([], [], task="asset_detection"),
            {"task": "asset_detection", "status": "not_evaluated", "sample_count": 0},
        )

    def test_ocr_reports_mean_cer_and_wer(self):
        report = evaluation_report(
            [{"text": "Tamil Nadu forest right"}, {"text": "claim 12/3"}],
            [{"text": "Tamil Nadu forest rights"}, {"text": "claim 12/3"}],
            task="ocr",
        )
        self.assertEqual(report["status"], "evaluated")
        self.assertEqual(report["sample_count"], 2)
        self.assertGreater(report["character_error_rate"], 0)
        self.assertGreater(report["word_error_rate"], 0)

    def test_entity_extraction_reports_per_label_and_macro_scores(self):
        report = evaluation_report(
            [{"district": "Thanjavur", "right_type": "IFR"}, {"district": "Salem", "right_type": "CFR"}],
            [{"district": "Thanjavur", "right_type": "CR"}, {"district": "Salem"}],
            task="entity_extraction",
        )
        self.assertEqual(set(report["per_label"]), {"district", "right_type"})
        self.assertEqual(report["per_label"]["district"]["f1"], 1.0)
        self.assertEqual(report["per_label"]["right_type"]["f1"], 0.0)
        self.assertEqual(report["macro"]["f1"], 0.5)

    def test_asset_classification_accepts_optional_iou_without_requiring_it(self):
        report = evaluation_report(
            [{"label": "water_body"}, {"label": "forest_cover"}],
            [{"label": "water_body", "iou": 0.8}, {"label": "water_body"}],
            task="asset_classification",
        )
        self.assertEqual(report["sample_count"], 2)
        self.assertEqual(report["mean_iou"], 0.8)
        self.assertIn("forest_cover", report["per_class"])


if __name__ == "__main__":
    unittest.main()
