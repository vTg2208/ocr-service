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

    def test_process_preprocesses_each_page_and_retains_page_level_results(self):
        class FakeImageProcessor:
            calls = 0

            @staticmethod
            def from_pil(page):
                return np.zeros((24, 32, 3), dtype=np.uint8)

            @classmethod
            def preprocess(cls, image):
                cls.calls += 1
                return np.full((24, 32), 255, dtype=np.uint8)

        class FakeOCREngine:
            @staticmethod
            def extract_text(image):
                if image.shape != (24, 32):
                    raise AssertionError("Expected a preprocessed grayscale page")
                return "page text", 90.0

        processor = PDFProcessor(FakeOCREngine(), FakeImageProcessor())
        processor._render_pages = lambda content: [Image.new("RGB", (32, 24), "white")]

        pages = processor.process_pages(b"pdf")
        text, confidence = processor.process(b"pdf")

        self.assertEqual(pages[0].page_number, 1)
        self.assertEqual(pages[0].text, "page text")
        self.assertEqual(pages[0].confidence, 90.0)
        self.assertEqual(text, "page text")
        self.assertEqual(confidence, 90.0)
        self.assertEqual(FakeImageProcessor.calls, 2)


if __name__ == "__main__":
    unittest.main()
