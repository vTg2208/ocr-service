import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import FRAArchiveRecord, FRAExtractionRun, FRAImportBatch, FRAVillageProfile, ModelVersion
from app.db.fra_models import FRAClaim, FRAGeometryVersion, RightsHolder
from app.db.fra_operational_models import ImageryArtifact, ImagerySceneRecord
from app.db.models import Document, User
from app.services.fra_reports import render_archive_report, render_historical_evidence_report, render_village_report


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

    def test_historical_report_is_neutral_versioned_escaped_and_redacted(self):
        with Session(self.engine) as session:
            owner = User(external_id="history-report-owner", display_name="Owner", role="user")
            reviewer = User(external_id="history-report-reviewer", display_name="Reviewer", role="reviewer")
            stranger = User(external_id="history-report-stranger", display_name="Stranger", role="user")
            holder = RightsHolder(display_name="Ramu", holder_type="individual")
            session.add_all([owner, reviewer, stranger, holder]); session.flush()
            claim = FRAClaim(claim_number="TN-HREP-1", right_type="IFR", status="submitted", rights_holder=holder, submitted_by=owner.id)
            session.add(claim); session.flush()
            geometry = FRAGeometryVersion(claim=claim, version=3, geometry=BOUNDARY, source="survey <office>", boundary_quality="surveyed", created_by=reviewer.id)
            scene = ImagerySceneRecord(provider="stac.example", collection="landsat-c2-l2", scene_id="scene-private", acquired_at=datetime(2005, 6, 1, tzinfo=timezone.utc), footprint=BOUNDARY, cloud_cover=7.5, asset_references_json={"visual": {"href": "https://signed.example/scene?secret=never"}}, license_reference="https://example.org/license", provenance_json={})
            model = ModelVersion(task="historical_evidence", adapter_type="rest", name="history", version="history-v2", status="active", configuration_json={"ready": True}, label_map_json={}, metrics_json={}, registered_by=reviewer.id)
            session.add_all([geometry, scene, model]); session.flush()
            session.add(ImageryArtifact(claim_id=claim.id, geometry_version_id=geometry.id, imagery_scene_id=scene.id, artifact_type="historical_land_observation:2005", target_year=2005, storage_key="private/history.json", content_sha256="a" * 64, processor_version="history-v2", model_version_id=model.id, parameters_json={}, statistics_json={"forest_index": 0.61, "note": "<script>bad()</script>"}, quality_flags_json=["cloud_screened"], provenance_json={"legal_role": "supporting_observation", "private_uri": "private://hidden"}, state="completed", verification_state="verified", reviewed_by=reviewer.id, reviewed_at=datetime.now(timezone.utc)))
            session.flush()

            with self.assertRaises(PermissionError):
                render_historical_evidence_report(session, claim.id, actor_id=stranger.id)
            html = render_historical_evidence_report(session, claim.id, actor_id=owner.id)
            for expected in ("Target year", "2005", "1 June 2005", "stac.example", "landsat-c2-l2", "7.5", "cloud screened", "Geometry version 3", "history-v2", "verified", "supporting evidence"):
                self.assertIn(expected, html)
            self.assertIn("&lt;script&gt;bad()&lt;/script&gt;", html)
            self.assertNotIn("private/history", html)
            self.assertNotIn("secret=never", html)
            self.assertNotIn("private://hidden", html)
            self.assertNotIn("proves tenure", html.casefold())


if __name__ == "__main__":
    unittest.main()
