import unittest
import re
from html.parser import HTMLParser
from pathlib import Path


UI_ROOT = Path(__file__).parents[1] / "app" / "static" / "land-mapping"
LOGIN_ROOT = Path(__file__).parents[1] / "app" / "static" / "login"


class StructureParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.steps = []
        self.staff_access_label = None
        self.elements_by_id = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(attributes["id"])
            self.elements_by_id[attributes["id"]] = {
                "tag": tag,
                "role": attributes.get("role"),
            }
        if tag == "li" and attributes.get("data-step"):
            self.steps.append(attributes["data-step"])
        if tag == "summary" and attributes.get("aria-label"):
            self.staff_access_label = attributes["aria-label"]


class LandMappingUITests(unittest.TestCase):
    def test_staff_workflow_has_three_real_stages_and_recovery_actions(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        parser = StructureParser()
        parser.feed(html)

        self.assertEqual(parser.steps, ["1", "2", "3"])
        self.assertTrue({
            "staffName", "logoutButton", "documentBar", "replaceButton",
            "evidenceToggle", "completionPanel", "startOverButton", "mapEmptyState",
        }.issubset(parser.ids))
        self.assertNotIn("Signed access token", html)
        self.assertNotIn("Staff access code", html)

    def test_login_page_has_one_access_code_form_and_inline_feedback(self):
        html = (LOGIN_ROOT / "index.html").read_text(encoding="utf-8")
        parser = StructureParser()
        parser.feed(html)

        self.assertTrue({"loginForm", "accessCode", "loginButton", "loginStatus"}.issubset(parser.ids))
        self.assertIn('inputmode="numeric"', html)
        self.assertIn('maxlength="4"', html)

    def test_confidence_badge_is_labeled_as_ocr_quality(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")

        match = re.search(r'<span\b[^>]*id="confidenceBadge"[^>]*>([^<]*)</span>', html)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "— OCR quality")

    def test_read_only_summary_values_expose_status_semantics(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        parser = StructureParser()
        parser.feed(html)

        for element_id in ("documentStatus", "confidenceBadge", "matchPill"):
            with self.subTest(element_id=element_id):
                self.assertEqual(parser.elements_by_id[element_id]["tag"], "span")
                self.assertEqual(parser.elements_by_id[element_id]["role"], "status")

    def test_claimed_land_view_has_navigation_persistent_map_and_evidence_action(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        parser = StructureParser()
        parser.feed(html)

        self.assertTrue({
            "newClaimTab", "claimedLandTab", "newClaimView", "claimedLandView",
            "claimedLandSummary", "claimedLandMap", "claimedLandEmpty",
            "claimedLandSearch", "claimedLandList", "claimedLandSearchEmpty",
            "viewClaimMapButton", "viewClaimedLandButton",
        }.issubset(parser.ids))
        self.assertEqual(parser.elements_by_id["claimedLandSearch"]["tag"], "input")
        self.assertEqual(parser.elements_by_id["claimedLandList"]["tag"], "ol")
        self.assertIn('/static/land-mapping/claimed-land.js?v=', html)
        self.assertIn('aria-label="Search registered claims"', html)


if __name__ == "__main__":
    unittest.main()
