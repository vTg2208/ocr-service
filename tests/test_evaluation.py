import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.services.evaluation import evaluate_ocr_text


class OCREvaluationTests(unittest.TestCase):
    def setUp(self):
        self.reference = "207/9 (0.04.00) 208/2B1 (0.08.50) 03.02.2024"
        self.prediction = "207/9 (0.01.00) 208/281 (0.08.50) 03.02.2024"

    def test_measures_critical_field_errors_separately(self):
        result = evaluate_ocr_text(self.reference, self.prediction)

        self.assertGreater(result.character_error_rate, 0)
        self.assertGreater(result.word_error_rate, 0)
        self.assertEqual(result.date_accuracy, 100.0)
        self.assertEqual(result.survey_number_accuracy, 50.0)
        self.assertEqual(result.numeric_field_accuracy, 60.0)
        self.assertEqual(result.critical_field_exact_match_accuracy, 0.0)

    def test_evaluate_endpoint_returns_metrics(self):
        response = TestClient(app).post(
            "/evaluate",
            data={"reference_text": self.reference, "ocr_text": self.prediction},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["date_accuracy"], 100.0)
        self.assertEqual(response.json()["critical_field_exact_match_accuracy"], 0.0)


if __name__ == "__main__":
    unittest.main()
