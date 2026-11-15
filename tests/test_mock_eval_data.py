import json
import unittest
from pathlib import Path

from scripts.evaluate_fra_models import evaluation_report

ROOT = Path(__file__).resolve().parents[1]


class MockEvalDataTests(unittest.TestCase):
    def test_mock_samples_are_labelled_and_evaluable(self):
        labels = json.loads((ROOT / "data/mock_eval/entity_labels.json").read_text(encoding="utf-8"))
        preds = json.loads((ROOT / "data/mock_eval/entity_predictions.json").read_text(encoding="utf-8"))
        self.assertTrue(all(item.get("mock") is True for item in labels + preds))
        report = evaluation_report(labels, preds, task="entity_extraction")
        self.assertEqual(report["status"], "evaluated")
        self.assertEqual(report["per_label"]["village"]["recall"], 0.5)
        self.assertEqual(report["data_kind"], "mock")
        self.assertFalse(report["benchmark_eligible"])
        self.assertNotIn("mock", report["per_label"])

    def test_mock_and_real_labels_cannot_be_mixed(self):
        with self.assertRaisesRegex(ValueError, "separately"):
            evaluation_report([{"mock":True,"text":"invented"},{"text":"real"}], [{"text":"a"},{"text":"b"}], task="ocr")

    def test_mock_predictions_cannot_be_mislabeled_as_a_real_benchmark(self):
        with self.assertRaisesRegex(ValueError, "Mock predictions"):
            evaluation_report([{"label":"water"}], [{"mock":True,"label":"water"}],task="asset_classification")

    def test_real_data_folder_has_no_mock_files(self):
        self.assertFalse(list((ROOT / "data/real").glob("*mock*")))


if __name__ == "__main__":
    unittest.main()
