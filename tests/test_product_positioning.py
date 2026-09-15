import unittest
from pathlib import Path

from app.main import app


class ProductPositioningTests(unittest.TestCase):
    def test_openapi_describes_the_fra_spatial_intelligence_platform(self):
        info = app.openapi()["info"]

        self.assertEqual(info["title"], "AranyaSetu")
        self.assertIn("FRA Spatial Intelligence and Decision Support", info["description"])
        self.assertNotIn("Standalone OCR", info["description"])

    def test_preferred_cadastral_route_is_presented_as_supporting_fra_evidence(self):
        schema = app.openapi()
        operation = schema["paths"]["/api/cadastral-evidence/documents/process"]["post"]

        self.assertEqual(operation["tags"], ["Supporting cadastral evidence"])
        self.assertEqual(
            operation["summary"],
            "Extract and match a supporting cadastral document",
        )
        self.assertNotIn("/api/pattas/process", schema["paths"])

    def test_api_catalogue_leads_with_fra_document_intelligence(self):
        paths = app.openapi()["paths"]
        operation = paths["/api/fra/document-intelligence/ocr"]["post"]

        self.assertEqual(operation["tags"], ["FRA document intelligence"])
        self.assertEqual(operation["summary"], "Digitize a scanned FRA or supporting document")
        self.assertNotIn("/ocr", paths)
        self.assertNotIn("/land/extract", paths)

    def test_runtime_and_deployment_names_use_aranyasetu_identity(self):
        root = Path(__file__).parents[1]
        main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
        compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")

        self.assertIn("name: aranyasetu", compose)
        self.assertIn("image: aranyasetu-api:local", compose)
        self.assertIn("image: aranyasetu-worker:local", compose)
        self.assertIn("/var/lib/aranyasetu/private_uploads", compose)
        self.assertIn("/var/lib/aranyasetu/private_uploads", dockerfile)
        self.assertIn('href="/cadastral-evidence"', (root / "app" / "static" / "fra" / "index.html").read_text(encoding="utf-8"))
        self.assertIn('@app.get("/cadastral-evidence"', main_source)
        self.assertNotIn("from app.api.patta_routes", main_source)
        self.assertNotIn("github.com/vTg2208/ocr-service", readme)


if __name__ == "__main__":
    unittest.main()
