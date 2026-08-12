import io
import unittest

import numpy as np
from PIL import Image

from app.services.pdf_processor import PDFProcessor


class PDFProcessorTests(unittest.TestCase):
    def test_render_pages_does_not_require_external_poppler(self):
        pdf = io.BytesIO()
        Image.new("RGB", (32, 24), "white").save(pdf, format="PDF")

        pages = PDFProcessor._render_pages(pdf.getvalue())

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].mode, "RGB")
        self.assertGreater(pages[0].width, 0)
        self.assertGreater(pages[0].height, 0)

    def test_process_sends_color_page_directly_to_paddle(self):
        class FakeImageProcessor:
            @staticmethod
            def from_pil(page):
                return np.zeros((24, 32, 3), dtype=np.uint8)

            @staticmethod
            def preprocess(image):
                raise AssertionError("PaddleOCR should receive the original color image")

        class FakeOCREngine:
            @staticmethod
            def extract_text(image):
                if image.shape != (24, 32, 3):
                    raise AssertionError("Expected an unmodified color page")
                return "page text", 90.0

        processor = PDFProcessor(FakeOCREngine(), FakeImageProcessor())
        processor._render_pages = lambda content: [Image.new("RGB", (32, 24), "white")]

        text, confidence = processor.process(b"pdf")

        self.assertEqual(text, "page text")
        self.assertEqual(confidence, 90.0)


if __name__ == "__main__":
    unittest.main()
