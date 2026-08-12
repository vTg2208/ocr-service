"""
Image preprocessing pipeline.

Applies a fixed sequence of OpenCV operations to maximize OCR accuracy:
grayscale -> upscale (if small) -> blur -> adaptive threshold ->
noise removal -> deskew. Kept as a standalone module so the pipeline can
be tuned or reordered without touching the OCR engine or API layer.
"""

import logging

import cv2
import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ImageProcessor:
    """Encapsulates the full preprocessing pipeline for a single image."""

    def __init__(self, min_width: int = None):
        self.min_width = min_width or settings.min_image_width

    def preprocess(self, image: np.ndarray) -> np.ndarray:
        """
        Run the full preprocessing pipeline on a BGR/gray OpenCV image
        and return an image ready to be handed to the OCR engine.
        """
        gray = self._to_grayscale(image)
        resized = self._resize_if_small(gray)
        blurred = self._gaussian_blur(resized)
        thresholded = self._adaptive_threshold(blurred)
        denoised = self._remove_noise(thresholded)
        deskewed = self._deskew(denoised)
        return deskewed

    @staticmethod
    def decode(content: bytes) -> np.ndarray:
        """Decode raw image bytes into an OpenCV (BGR) image, in memory."""
        np_arr = np.frombuffer(content, dtype=np.uint8)
        image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Unable to decode image data.")
        return image

    @staticmethod
    def from_pil(pil_image) -> np.ndarray:
        """Convert a PIL image (e.g. a pdf2image page) to an OpenCV image."""
        rgb = np.array(pil_image.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    # --- Pipeline steps -------------------------------------------------

    @staticmethod
    def _to_grayscale(image: np.ndarray) -> np.ndarray:
        if len(image.shape) == 2:
            return image
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    def _resize_if_small(self, image: np.ndarray) -> np.ndarray:
        height, width = image.shape[:2]
        if width >= self.min_width:
            return image
        scale = self.min_width / float(width)
        new_size = (self.min_width, int(height * scale))
        return cv2.resize(image, new_size, interpolation=cv2.INTER_CUBIC)

    @staticmethod
    def _gaussian_blur(image: np.ndarray) -> np.ndarray:
        return cv2.GaussianBlur(image, (5, 5), 0)

    @staticmethod
    def _adaptive_threshold(image: np.ndarray) -> np.ndarray:
        return cv2.adaptiveThreshold(
            image,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            11,
        )

    @staticmethod
    def _remove_noise(image: np.ndarray) -> np.ndarray:
        kernel = np.ones((1, 1), np.uint8)
        opened = cv2.morphologyEx(image, cv2.MORPH_OPEN, kernel)
        return cv2.medianBlur(opened, 3)

    # Real-world document skew is rarely more than this. Angles beyond it
    # are almost always a degenerate minAreaRect reading on near-axis-
    # aligned text (not real skew), so we treat them as "no skew found"
    # rather than applying a destructive rotation.
    _MAX_CORRECTABLE_SKEW_DEGREES = 15.0

    @classmethod
    def _deskew(cls, image: np.ndarray) -> np.ndarray:
        """
        Estimate and correct skew using the minimum-area bounding box of
        foreground (text) pixels. Falls back to the original image if no
        meaningful (or plausible) skew can be determined.
        """
        inverted = cv2.bitwise_not(image)
        coords = np.column_stack(np.where(inverted > 0))
        if coords.shape[0] < 20:
            return image

        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle

        # Skip correction for negligible skew, and for implausibly large
        # angles that indicate a degenerate/ambiguous bounding-box reading
        # rather than genuine rotation (common with wide, sparse text).
        if abs(angle) < 0.5 or abs(angle) > cls._MAX_CORRECTABLE_SKEW_DEGREES:
            return image

        (h, w) = image.shape[:2]
        center = (w // 2, h // 2)
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(
            image,
            matrix,
            (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        return rotated
