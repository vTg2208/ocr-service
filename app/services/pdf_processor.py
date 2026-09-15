"""
PDF processing pipeline.

Converts each page of a PDF to an image (via PDFium), runs
each page through the same preprocessing + OCR pipeline used for plain
images, then merges the per-page text into a single result.
"""

import logging
from dataclasses import dataclass, field
from typing import Tuple

import pypdfium2 as pdfium

from app.config import get_settings
from app.services.image_processor import ImageProcessor
from app.services.ocr_engine import OCRException, OCRService

logger = logging.getLogger(__name__)
settings = get_settings()


class PDFProcessingError(Exception):
    """Raised when a PDF cannot be parsed/rendered. Maps to HTTP 400."""


@dataclass(frozen=True)
class OCRPageResult:
    page_number: int
    text: str
    confidence: float
    lines: list[dict] = field(default_factory=list)


class PDFProcessor:
    """Runs OCR across every page of a PDF and merges the results."""

    def __init__(self, ocr_engine: OCRService, image_processor: ImageProcessor = None):
        self.ocr_engine = ocr_engine
        self.image_processor = image_processor or ImageProcessor()

    def process(self, pdf_bytes: bytes) -> Tuple[str, float]:
        page_results = self.process_pages(pdf_bytes)
        merged_text = "\n\n".join(page.text for page in page_results)
        average_confidence = (
            round(sum(page.confidence for page in page_results) / len(page_results), 2)
            if page_results else 0.0
        )
        return merged_text, average_confidence

    def process_pages(self, pdf_bytes: bytes) -> list[OCRPageResult]:
        pages = self._render_pages(pdf_bytes)
        results: list[OCRPageResult] = []

        for page_number, pil_page in enumerate(pages, start=1):
            try:
                cv_image = self.image_processor.from_pil(pil_page)
                prepared = self.image_processor.preprocess(cv_image)
                if hasattr(self.ocr_engine, "extract_page"):
                    analysis = self.ocr_engine.extract_page(prepared)
                    text, confidence = analysis.text, analysis.confidence
                    lines = [
                        {
                            "text": line.text,
                            "confidence": line.confidence,
                            "bounding_box": line.bounding_box,
                        }
                        for line in analysis.lines
                    ]
                else:
                    text, confidence = self.ocr_engine.extract_text(prepared)
                    lines = []
            except OCRException:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed processing PDF page %d: %s", page_number, exc)
                raise OCRException("OCR processing failed.") from exc

            results.append(OCRPageResult(page_number, text, confidence, lines))
        return results

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
