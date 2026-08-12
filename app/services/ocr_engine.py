"""
OCR engine abstraction.

`OCRService` defines the interface every engine implementation must
satisfy. PaddleOCR is the default implementation.
"""

import logging
from abc import ABC, abstractmethod
import os
from typing import Tuple

import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class OCRException(Exception):
    """Raised when text extraction fails. Maps to HTTP 500."""


class OCRInitializationError(OCRException):
    """Raised when an OCR engine cannot be initialized."""


class OCRService(ABC):
    """Interface for any OCR engine implementation."""

    @abstractmethod
    def extract_text(self, image: np.ndarray) -> Tuple[str, float]:
        """
        Run OCR on a preprocessed image.

        Returns a tuple of (extracted_text, average_confidence_0_to_100).
        """
        raise NotImplementedError

class PaddleOCREngine(OCRService):
    def __init__(self):
        # PaddlePaddle 3.3.1 has a known CPU inference crash when PIR and
        # oneDNN are combined for PP-OCRv5 models.
        os.environ["FLAGS_enable_pir_api"] = "0"
        from paddleocr import PaddleOCR

        init_kwargs = {
            "enable_mkldnn": False,
            "text_detection_model_name": settings.paddleocr_detection_model_name,
            "text_recognition_model_name": settings.paddleocr_recognition_model_name,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
        }
        det_model_dir = os.getenv("PADDLEOCR_DET_MODEL_DIR")
        rec_model_dir = os.getenv("PADDLEOCR_REC_MODEL_DIR")
        if det_model_dir:
            init_kwargs["text_detection_model_dir"] = det_model_dir
        if rec_model_dir:
            init_kwargs["text_recognition_model_dir"] = rec_model_dir

        try:
            self.engine = PaddleOCR(**init_kwargs)
        except Exception as exc:
            logger.error("Failed to initialize PaddleOCR: %s", exc)
            raise OCRInitializationError(
                "PaddleOCR could not initialize. Verify paddlepaddle is installed and model files are accessible."
            ) from exc

    def extract_text(self, image: np.ndarray) -> Tuple[str, float]:
        try:
            if image.ndim == 2:
                image = np.repeat(image[:, :, np.newaxis], 3, axis=2)
            results = self.engine.predict(image)
            lines = []
            confidences = []
            for page in results:
                texts = page.get("rec_texts", [])
                scores = page.get("rec_scores", [])
                for value, score in zip(texts, scores):
                    text = str(value).strip()
                    if not text:
                        continue
                    lines.append(text)
                    confidences.append(float(score) * 100)
        except Exception as exc:
            logger.error("PaddleOCR inference failed: %s", exc)
            raise OCRException("OCR processing failed.") from exc

        extracted_text = "\n".join(lines)
        average_confidence = (
            round(sum(confidences) / len(confidences), 2) if confidences else 0.0
        )
        return extracted_text, average_confidence
