import unittest
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import FRAArchiveRecord, FRAExtractionRun, FRAImportBatch, FRAVillageProfile
from app.db.models import Document, User
from app.services.fra_reports import render_archive_report, render_village_report


BOUNDARY = {
    "type": "MultiPolygon",
    "coordinates": [[[[79.1, 10.7], [79.12, 10.7], [79.12, 10.72], [79.1, 10.72], [79.1, 10.7]]]],
}


class FRAReportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_village_report_has_provenance_and_mandatory_warnings(self):
        with Session(self.engine) as session:
            planner = User(external_id="report-planner", display_name="Planner", role="reviewer")
            village = FRAVillageProfile(
                state_code="TN", state_name="Tamil Nadu", district_code="TN-13",
                district_name="Thanjavur", block_code="TN-13-01", block_name="Kumbakonam",
                village_code="TN-13-01-001", village_name="Kottur <Sample>", boundary=BOUNDARY,
                tribal_groups_json=["Synthetic community"], socioeconomic_json={"water_access": "documented"},
                provenance_json={"synthetic": True, "source": "private://must-redact"},
                reference_version="tn-sample-1", synthetic=True,
            )
            session.add_all([planner, village]); session.flush()

            html = render_village_report(session, village.id, actor_id=planner.id)

            self.assertIn("Synthetic sample data", html)
            self.assertIn("supporting evidence and do not determine legal validity", html)
            self.assertIn("advisory and do not approve or sanction benefits", html)
            self.assertNotIn("private://", html)
            self.assertIn("Kottur &lt;Sample&gt;", html)
            self.assertNotIn("demonstration", html.casefold())
            self.assertIn("@media print", html)

    def test_archive_report_requires_reviewer_and_escapes_raw_text(self):
        with Session(self.engine) as session:
            staff = User(external_id="report-staff", display_name="Staff", role="user")
            reviewer = User(external_id="report-reviewer", display_name="Reviewer", role="reviewer")
            session.add_all([staff, reviewer]); session.flush()
            document = Document(
                uploaded_by=staff.id, storage_key="private/archive.pdf", original_filename="archive.pdf",
                content_type="application/pdf", sha256="e" * 64, idempotency_key="report-doc",
            )
            batch = FRAImportBatch(
                source_label="Synthetic pack", state_code="TN", created_by=staff.id,
                idempotency_key="report-batch", synthetic=True,
            )
            record = FRAArchiveRecord(
                batch=batch, document=document, legacy_reference="TN-REPORT-1", state_code="TN",
                review_state="needs_review", synthetic=True,
            )
            record.extraction_runs.append(
                FRAExtractionRun(
                    raw_text="<script>alert('x')</script>", standardized_json={},
                    field_evidence_json={}, provenance_json={"synthetic": True},
                )
            )
            session.add(record); session.flush()

            with self.assertRaises(PermissionError):
                render_archive_report(session, record.id, actor_id=staff.id)
            html = render_archive_report(session, record.id, actor_id=reviewer.id)
            self.assertNotIn("<script>", html)
            self.assertIn("&lt;script&gt;", html)
            self.assertNotIn("private/archive.pdf", html)


if __name__ == "__main__":
    unittest.main()
