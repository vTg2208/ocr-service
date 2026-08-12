import io
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from PIL import Image

from app.api import land_routes
from app.main import app
from app.models.response_models import OCRResponse
from app.services.land_enrichment import LandEnrichmentService
from app.services.quality_assessment import assess_ocr_quality


def png_bytes():
    content = io.BytesIO()
    Image.new("RGB", (32, 24), "white").save(content, format="PNG")
    return content.getvalue()


def base_ocr_response():
    return OCRResponse(
        success=True,
        filename="sample.png",
        processing_time=0.1,
        text="base OCR text",
        confidence=91.5,
        quality=assess_ocr_quality("base OCR text", 91.5),
    )


class LandRouteTests(unittest.TestCase):
    def test_land_extract_accepts_existing_ocr_text_without_rerunning_ocr(self):
        service = LandEnrichmentService(client=None, model_name="unused")
        with patch.object(land_routes, "_land_service", service):
            response = TestClient(app).post(
                "/land/extract",
                data={"ocr_text": "207/9 (0.04.00)"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["records"][0]["survey_number"]["value"],
            "207/9",
        )

    def test_rejects_empty_ocr_text(self):
        response = TestClient(app).post(
            "/land/extract",
            data={"ocr_text": "   "},
        )
        self.assertEqual(response.status_code, 400)


class OCRLandFailureIsolationTests(unittest.TestCase):
    def test_ocr_land_keeps_successful_ocr_when_enrichment_fails(self):
        with patch(
            "app.api.land_routes.ocr_endpoint",
            new=AsyncMock(return_value=base_ocr_response()),
        ):
            with patch(
                "app.api.land_routes._land_service.extract",
                new=AsyncMock(side_effect=RuntimeError("provider down")),
            ):
                response = TestClient(app).post(
                    "/ocr/land",
                    files={"file": ("sample.png", png_bytes(), "image/png")},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ocr"]["text"], "base OCR text")
        self.assertEqual(response.json()["land_extraction"]["status"], "failed")
        self.assertNotIn("provider down", response.json()["land_extraction"]["error"])


class StandaloneOCRIndependenceTests(unittest.TestCase):
    def test_ocr_does_not_invoke_land_enrichment(self):
        class FakeOCREngine:
            @staticmethod
            def extract_text(image):
                return "base OCR text", 91.5

        with patch(
            "app.api.routes._get_or_raise_ocr_engine",
            return_value=FakeOCREngine(),
        ):
            with patch(
                "app.api.land_routes._land_service.extract",
                side_effect=AssertionError("standalone OCR invoked enrichment"),
            ):
                response = TestClient(app).post(
                    "/ocr",
                    files={"file": ("sample.png", png_bytes(), "image/png")},
                )
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
