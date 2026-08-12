import os
import unittest
from types import ModuleType
from unittest.mock import patch

import numpy as np

from app.services.ocr_engine import PaddleOCREngine


class _PaddleV3Result(dict):
    pass


class _FakePaddleEngine:
    last_image_shape = None

    def predict(self, image):
        self.last_image_shape = image.shape
        return [
            _PaddleV3Result(
                rec_texts=["First line", "Second line"],
                rec_scores=np.array([0.98, 0.86]),
            )
        ]


class PaddleOCREngineTests(unittest.TestCase):
    def test_initializes_paddleocr_with_tamil_mobile_models(self):
        captured = {}

        class FakePaddleOCR:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_module = ModuleType("paddleocr")
        fake_module.PaddleOCR = FakePaddleOCR

        with patch.dict("sys.modules", {"paddleocr": fake_module}):
            PaddleOCREngine()

        self.assertEqual(captured["text_detection_model_name"], "PP-OCRv5_mobile_det")
        self.assertEqual(captured["text_recognition_model_name"], "ta_PP-OCRv5_mobile_rec")

    def test_disables_broken_pir_on_cpu(self):
        captured = {}

        class FakePaddleOCR:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_module = ModuleType("paddleocr")
        fake_module.PaddleOCR = FakePaddleOCR

        with patch.dict(os.environ, {"FLAGS_enable_pir_api": "1"}):
            with patch.dict("sys.modules", {"paddleocr": fake_module}):
                PaddleOCREngine()

            self.assertEqual(os.environ["FLAGS_enable_pir_api"], "0")
        self.assertFalse(captured["enable_mkldnn"])

    def test_extract_text_parses_paddleocr_v3_results(self):
        engine = PaddleOCREngine.__new__(PaddleOCREngine)
        engine.engine = _FakePaddleEngine()

        text, confidence = engine.extract_text(np.zeros((10, 10), dtype=np.uint8))

        self.assertEqual(text, "First line\nSecond line")
        self.assertEqual(confidence, 92.0)
        self.assertEqual(engine.engine.last_image_shape, (10, 10, 3))


if __name__ == "__main__":
    unittest.main()
