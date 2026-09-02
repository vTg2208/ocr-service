import unittest
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app


UI_ROOT = Path(__file__).parents[1] / "app" / "static" / "fra"


class WorkspaceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.nav_sections = []
        self.landmarks = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if values.get("data-section"):
            self.nav_sections.append(values["data-section"])
        if tag in {"nav", "main", "header", "aside"}:
            self.landmarks.append(tag)


class FRAWorkspaceUITests(unittest.TestCase):
    def test_workspace_has_a_clear_back_action_to_the_patta_registry(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertIn('class="rail-secondary" href="/land-mapping"', html)
        self.assertIn('class="rail-secondary-icon"', html)
        self.assertIn("Back to Patta Registry", html)

    def test_workspace_has_six_sections_shared_context_and_archive_review_regions(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        parser = WorkspaceParser(); parser.feed(html)

        self.assertEqual(parser.nav_sections, ["archive", "cases", "atlas", "assets", "planner", "reports"])
        self.assertTrue({
            "skipLink", "workspaceNav", "contextDistrict", "contextBlock", "contextVillage",
            "staffName", "logoutButton", "archiveSearch", "archiveList", "archiveEmpty",
            "archiveDetail", "sourceEvidence", "reviewForm", "reviewedFields",
            "saveReviewButton", "promoteButton", "workspaceStatus",
            "archiveUploadForm", "archiveSourceOffice", "archiveUploadDistrict",
            "archiveFiles", "archiveUploadButton", "archiveUploadResults",
        }.issubset(parser.ids))
        self.assertTrue({"nav", "main", "header", "aside"}.issubset(parser.landmarks))
        self.assertNotIn('class="warning-strip"', html)
        self.assertNotIn("Synthetic sample data — not authoritative", html)
        self.assertNotIn("demonstration", html.casefold())
        self.assertNotIn("approved benefit", html.casefold())

    def test_workspace_uses_modular_scripts_and_leaflet(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        for script in (
            "api.js", "app.js", "archive.js", "cases.js", "atlas.js", "assets.js", "planner.js", "reports.js"
        ):
            self.assertIn(f"/static/fra/{script}", html)
        self.assertIn("leaflet", html.casefold())

    def test_workspace_navigation_uses_supplied_section_icons(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        icon_names = ("archieve.png", "atlas.png", "assets.png", "planner.png", "reports.png")
        for icon_name in icon_names:
            with self.subTest(icon=icon_name):
                self.assertIn(f'/static/fra/icons/{icon_name}', html)
                self.assertTrue((UI_ROOT / "icons" / icon_name).is_file())
        self.assertEqual(html.count('class="rail-icon"'), 6)

    def test_cases_workspace_exposes_intake_case_and_versioned_casework_controls(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        parser = WorkspaceParser(); parser.feed(html)

        required = {
            "casesPanel", "caseModeIntake", "caseModeCases", "intakeList", "caseList",
            "caseDetail", "caseGeometryForm", "caseEvidenceForm", "caseTransitionForm",
            "caseTitleForm", "caseAuditTimeline",
        }
        self.assertTrue(required <= parser.ids)
        self.assertIn('data-section="cases"', html)
        self.assertIn("/static/fra/cases.js", html)

    def test_asset_workspace_uses_the_supplied_contact_sheet_and_accessible_legend(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        css = (UI_ROOT / "styles.css").read_text(encoding="utf-8")
        parser = WorkspaceParser(); parser.feed(html)

        self.assertIn("assetLegend", parser.ids)
        self.assertIn('aria-label="Asset map legend"', html)
        self.assertIn("/static/fra/icons/assets-sprite.png", css)
        self.assertTrue((UI_ROOT / "icons" / "assets-sprite.png").is_file())
        self.assertIn(".asset-map-marker", css)
        self.assertIn(".asset-record-glyph", css)
        self.assertIn(".asset-record-glyph { width: 32px", css)
        self.assertIn(".asset-map-marker { width: 34px", css)
        self.assertIn(".asset-legend-glyph { width: 24px", css)
        self.assertIn(".atlas-asset-glyph { width: 28px", css)
        self.assertLess(html.index("/static/fra/assets.js"), html.index("/static/fra/atlas.js"))

    def test_remaining_workspaces_have_real_controls_maps_lists_and_warnings(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        parser = WorkspaceParser(); parser.feed(html)
        self.assertTrue({
            "atlasFilters", "atlasMap", "atlasSummary", "atlasResults", "atlasLayers",
            "assetInferenceForm", "assetModel", "assetVillage", "assetList", "assetMap",
            "recommendationFilters", "recommendationList", "referralDepartment",
            "reportVillage", "reportArchive", "openVillageReport", "openArchiveReport",
        }.issubset(parser.ids))
        self.assertIn("supporting evidence and requires human verification", html)
        self.assertIn("does not approve or sanction benefits", html)
        self.assertIn("Print / Save as PDF", html)
        self.assertIn("Awaiting trained model", html)
        self.assertIn('placeholder="All tribal groups"', html)
        self.assertIn('placeholder="All years"', html)
        self.assertIn('value="TN-FRA-WATER-SUPPORT"', html)
        self.assertIn('<option value="recommended">Recommended</option>', html)
        self.assertIn('<option value="not_recommended">Not recommended</option>', html)
        self.assertNotIn('<option value="eligible">', html)
        self.assertIn('<option value="submitted">Submitted</option>', html)
        self.assertNotIn('<option value="filed">', html)

    def test_styles_have_existing_palette_visible_focus_and_mobile_layout(self):
        css = (UI_ROOT / "styles.css").read_text(encoding="utf-8")
        self.assertIn("--paper-100: oklch(96.5% .012 155);", css)
        self.assertIn("--green-800: oklch(35% .09 155);", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("min-height: 44px", css)
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertNotIn("linear-gradient", css)

    def test_desktop_workspace_context_is_inset_but_mobile_remains_edge_to_edge(self):
        css = (UI_ROOT / "styles.css").read_text(encoding="utf-8")

        self.assertIn(".application { min-width: 0; padding-top: var(--space-4); }", css)
        self.assertIn("margin: 0 var(--space-5)", css)
        self.assertIn(".application { padding-top: 0; }", css)
        self.assertIn(".context-bar { margin: 0;", css)
        self.assertNotIn("backdrop-filter", css)

    def test_fra_route_redirects_anonymous_and_serves_authenticated_session(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)

        def db_override():
            with factory() as session:
                yield session

        app.dependency_overrides[get_db] = db_override
        try:
            with TestClient(app) as client:
                anonymous = client.get("/fra", follow_redirects=False)
                self.assertEqual(anonymous.status_code, 307)
                self.assertEqual(anonymous.headers["location"], "/login")
                self.assertEqual(client.post("/api/auth/demo-login", json={"access_code": "1234"}).status_code, 200)
                authenticated = client.get("/fra")
                self.assertEqual(authenticated.status_code, 200)
                self.assertIn("Tamil Nadu FRA Platform", authenticated.text)
        finally:
            app.dependency_overrides.clear()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
