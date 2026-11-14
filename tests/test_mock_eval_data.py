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

    def test_real_data_folder_has_no_mock_files(self):
        self.assertFalse(list((ROOT / "data/real").glob("*mock*")))


if __name__ == "__main__":
    unittest.main()
