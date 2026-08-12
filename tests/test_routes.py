import io
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app.api import routes
from app.main import app


class OCRRouteTests(unittest.TestCase):
    def test_image_upload_sends_original_color_image_to_paddle(self):
        class FakeOCREngine:
            @staticmethod
            def extract_text(image):
                if image.shape != (24, 32, 3):
                    raise AssertionError("Expected the decoded color image")
                return "detected text", 91.5

        content = io.BytesIO()
        Image.new("RGB", (32, 24), "white").save(content, format="PNG")

        with patch.object(routes, "_get_or_raise_ocr_engine", return_value=FakeOCREngine()):
            with patch.object(
                routes._image_processor,
                "preprocess",
                side_effect=AssertionError("PaddleOCR should receive the original color image"),
            ):
                response = TestClient(app).post(
                    "/ocr",
                    files={"file": ("sample.png", content.getvalue(), "image/png")},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["text"], "detected text")
        self.assertEqual(response.json()["quality"]["model_confidence"], 91.5)
        self.assertFalse(response.json()["quality"]["confidence_is_text_accuracy"])


if __name__ == "__main__":
    unittest.main()
