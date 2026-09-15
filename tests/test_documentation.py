import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class DocumentationTests(unittest.TestCase):
    def test_architecture_guide_covers_the_required_fra_system_topics(self):
        guide = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        required_headings = [
            "## Problem and scope",
            "## System architecture",
            "## Data model",
            "## Legacy ingestion pipeline",
            "## OCR and FRA entity extraction",
            "## Spatial architecture",
            "## Satellite intelligence",
            "## Asset taxonomy and village profiles",
            "## FRA Atlas",
            "## DSS and scheme rules",
            "## Security and authorization",
            "## Provenance",
            "## Deployment and operations",
            "## Current limitations",
        ]
        for heading in required_headings:
            self.assertIn(heading, guide)
        self.assertIn("fra-assets-v1", guide)
        self.assertIn("fra-dss-facts-v1", guide)
        self.assertIn("20260906_0013", guide)
        self.assertIn("awaiting_user_model", guide)

    def test_current_guides_do_not_claim_superseded_runtime_behavior(self):
        foundation = (ROOT / "docs" / "FRA_FOUNDATION.md").read_text(encoding="utf-8")
        operations = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("docs/ARCHITECTURE.md", readme)
        self.assertNotIn("does not fetch imagery", foundation)
        self.assertNotIn("has not been expanded", foundation)
        self.assertNotIn("20260902_0005", foundation + operations)
        self.assertIn("20260906_0013", foundation + operations)

    def test_demo_guide_contains_the_ordered_seventeen_step_fra_journey(self):
        guide = (ROOT / "docs" / "DEMO_FLOW.md").read_text(encoding="utf-8")

        rows = [line for line in guide.splitlines() if line.startswith("| ")]
        numbered = [int(line.split("|")[1].strip()) for line in rows if line.split("|")[1].strip().isdigit()]
        self.assertEqual(numbered, list(range(1, 18)))
        self.assertLess(guide.index("legacy FRA documents"), guide.index("FRA Atlas"))
        self.assertLess(guide.index("FRA Atlas"), guide.index("AI asset detection"))
        self.assertLess(guide.index("AI asset detection"), guide.index("DSS facts"))
        self.assertLess(guide.index("DSS facts"), guide.index("Generate a report"))
        self.assertIn("awaiting_user_model", guide)

    def test_final_outcome_audit_accounts_for_every_priority_without_hiding_future_scope(self):
        audit = (ROOT / "docs" / "FINAL_OUTCOME_AUDIT.md").read_text(encoding="utf-8")

        for priority, start, end in (("P0", 1, 11), ("P1", 12, 20), ("P2", 21, 28)):
            for number in range(start, end + 1):
                self.assertEqual(audit.count(f"| {priority}-{number} |"), 1)
        self.assertIn("P0-7 | Real satellite AI pipeline | verified_with_user_model_placeholder", audit)
        self.assertIn("P1-20 | Real Tamil Nadu pilot dataset | verified_with_disclosed_source_gaps", audit)
        self.assertIn("P2-26 | Mobile feedback | future_scope", audit)
        self.assertIn("P2-27 | IoT integration | future_scope", audit)
        self.assertIn("P2-28 | Real-time satellite monitoring | future_scope", audit)


if __name__ == "__main__":
    unittest.main()
