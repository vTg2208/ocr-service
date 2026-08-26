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
    def test_workspace_has_five_sections_shared_context_and_archive_review_regions(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        parser = WorkspaceParser(); parser.feed(html)

        self.assertEqual(parser.nav_sections, ["archive", "atlas", "assets", "planner", "reports"])
        self.assertTrue({
            "skipLink", "workspaceNav", "contextDistrict", "contextBlock", "contextVillage",
            "staffName", "logoutButton", "archiveSearch", "archiveList", "archiveEmpty",
            "archiveDetail", "sourceEvidence", "reviewForm", "reviewedFields",
            "saveReviewButton", "promoteButton", "workspaceStatus",
        }.issubset(parser.ids))
        self.assertTrue({"nav", "main", "header", "aside"}.issubset(parser.landmarks))
        self.assertIn("Synthetic demonstration data — not authoritative", html)
        self.assertIn("Supporting observations do not determine legal validity", html)
        self.assertNotIn("approved benefit", html.casefold())

    def test_workspace_uses_modular_scripts_and_leaflet(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        for script in ("api.js", "app.js", "archive.js"):
            self.assertIn(f"/static/fra/{script}", html)
        self.assertIn("leaflet", html.casefold())

    def test_styles_have_existing_palette_visible_focus_and_mobile_layout(self):
        css = (UI_ROOT / "styles.css").read_text(encoding="utf-8")
        self.assertIn("--paper-100: oklch(96.5% .012 155);", css)
        self.assertIn("--green-800: oklch(35% .09 155);", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("min-height: 44px", css)
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertNotIn("linear-gradient", css)
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
