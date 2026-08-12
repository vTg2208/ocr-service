"""
PDF processing pipeline.

Converts each page of a PDF to an image (via PDFium), runs
each page through the same preprocessing + OCR pipeline used for plain
images, then merges the per-page text into a single result.
"""

import logging
from typing import List, Tuple

import pypdfium2 as pdfium

from app.config import get_settings
from app.services.image_processor import ImageProcessor
from app.services.ocr_engine import OCRException, OCRService

logger = logging.getLogger(__name__)
settings = get_settings()


class PDFProcessingError(Exception):
    """Raised when a PDF cannot be parsed/rendered. Maps to HTTP 400."""


class PDFProcessor:
    """Runs OCR across every page of a PDF and merges the results."""

    def __init__(self, ocr_engine: OCRService, image_processor: ImageProcessor = None):
        self.ocr_engine = ocr_engine
        self.image_processor = image_processor or ImageProcessor()

    def process(self, pdf_bytes: bytes) -> Tuple[str, float]:
        pages = self._render_pages(pdf_bytes)

        page_texts: List[str] = []
        page_confidences: List[float] = []

        for page_number, pil_page in enumerate(pages, start=1):
            try:
                cv_image = self.image_processor.from_pil(pil_page)
                text, confidence = self.ocr_engine.extract_text(cv_image)
            except OCRException:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed processing PDF page %d: %s", page_number, exc)
                raise OCRException("OCR processing failed.") from exc

            page_texts.append(text)
            page_confidences.append(confidence)

        merged_text = "\n\n".join(page_texts)
        average_confidence = (
            round(sum(page_confidences) / len(page_confidences), 2) if page_confidences else 0.0
        )
        return merged_text, average_confidence

    @staticmethod
    def _render_pages(pdf_bytes: bytes):
        try:
            document = pdfium.PdfDocument(pdf_bytes)
            scale = settings.pdf_dpi / 72.0
            pages = []
            try:
                for index in range(len(document)):
                    page = document[index]
                    try:
                        bitmap = page.render(scale=scale)
                        pages.append(bitmap.to_pil().convert("RGB").copy())
                    finally:
                        page.close()
            finally:
                document.close()

            if not pages:
                raise PDFProcessingError("Uploaded PDF contains no pages.")
            return pages
        except PDFProcessingError:
            raise
        except Exception as exc:
            logger.error("Invalid PDF file: %s", exc)
            raise PDFProcessingError("Uploaded PDF is corrupted or unreadable.") from exc
