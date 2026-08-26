import unittest
import re
from html.parser import HTMLParser
from pathlib import Path


UI_ROOT = Path(__file__).parents[1] / "app" / "static" / "land-mapping"
LOGIN_ROOT = Path(__file__).parents[1] / "app" / "static" / "login"
BRAND_ROOT = Path(__file__).parents[1] / "app" / "static" / "brand"


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

    def test_current_document_is_inside_document_fields_during_review(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")

        field_sheet = re.search(
            r'<article class="field-sheet">(?P<body>.*?)</article>', html, re.DOTALL
        )
        self.assertIsNotNone(field_sheet)
        self.assertIn('id="documentBar"', field_sheet.group("body"))
        self.assertLess(
            field_sheet.group("body").index('id="documentBar"'),
            field_sheet.group("body").index('id="fieldForm"'),
        )

    def test_current_document_filename_can_wrap_without_hiding_its_identity(self):
        css = (UI_ROOT / "styles.css").read_text(encoding="utf-8")

        rule = re.search(r"\.document-name strong\s*\{(?P<body>[^}]*)\}", css)
        self.assertIsNotNone(rule)
        self.assertIn("white-space: normal", rule.group("body"))
        self.assertIn("overflow-wrap: anywhere", rule.group("body"))

    def test_ocr_quality_is_a_bottom_caution_not_a_heading_badge(self):
        html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        field_sheet = re.search(
            r'<article class="field-sheet">(?P<body>.*?)</article>', html, re.DOTALL
        )
        self.assertIsNotNone(field_sheet)
        body = field_sheet.group("body")

        heading = re.search(r'<div class="section-heading">(?P<body>.*?)</div>', body, re.DOTALL)
        self.assertIsNotNone(heading)
        self.assertNotIn('id="confidenceBadge"', heading.group("body"))
        self.assertIn('id="ocrCaution"', body)
        self.assertIn("OCR extraction is not perfect", body)
        self.assertGreater(body.index('id="ocrCaution"'), body.index('id="fieldForm"'))

    def test_aranyasetu_brand_uses_the_supplied_static_emblem(self):
        mapping_html = (UI_ROOT / "index.html").read_text(encoding="utf-8")
        login_html = (LOGIN_ROOT / "index.html").read_text(encoding="utf-8")
        emblem_path = "/static/brand/aranyasetu-emblem.png"

        for page, html in (("mapping", mapping_html), ("login", login_html)):
            with self.subTest(page=page):
                self.assertIn("AranyaSetu", html)
                self.assertIn(f'src="{emblem_path}"', html)
                self.assertNotIn("Parcel Ledger", html)
        self.assertTrue((BRAND_ROOT / "aranyasetu-emblem.png").is_file())

    def test_brand_name_precedes_the_cadastral_registry_subtitle(self):
        for page, html_path in (
            ("mapping", UI_ROOT / "index.html"),
            ("login", LOGIN_ROOT / "index.html"),
        ):
            html = html_path.read_text(encoding="utf-8")
            brand = re.search(r'<span class="brand-copy">(?P<body>.*?)</span>', html, re.DOTALL)
            self.assertIsNotNone(brand)
            with self.subTest(page=page):
                body = brand.group("body")
                self.assertIn("a central cadastral registry", body)
                self.assertLess(body.index("AranyaSetu"), body.index("a central cadastral registry"))

    def test_aranyasetu_pages_use_the_emblem_led_green_palette(self):
        for page, css_path in (
            ("mapping", UI_ROOT / "styles.css"),
            ("login", LOGIN_ROOT / "styles.css"),
        ):
            css = css_path.read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertIn("--paper-100: oklch(96.5% .012 155);", css)
                self.assertIn("--green-800: oklch(35% .09 155);", css)
                self.assertIn("--focus-600: oklch(52% .11 172);", css)
                self.assertNotIn("--rust-", css)

    def test_low_confidence_fields_use_warning_colours_not_a_decorative_accent(self):
        css = (UI_ROOT / "styles.css").read_text(encoding="utf-8")

        low_field_rule = re.search(r"\.field-grid label\.low input\s*\{(?P<body>[^}]*)\}", css)
        self.assertIsNotNone(low_field_rule)
        self.assertIn("var(--warning-700)", low_field_rule.group("body"))
        self.assertIn("var(--warning-100)", low_field_rule.group("body"))

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
