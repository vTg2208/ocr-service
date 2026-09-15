import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db import fra_completion_models
from app.db.fra_completion_models import (
    FRAArchiveRecord,
    FRAExtractionRun,
    FRAImportBatch,
    FRAVillageProfile,
    ModelVersion,
)
from app.db.fra_models import (
    FRADecision,
    FRAClaim,
    FRAEvidenceItem,
    FRAGeometryVersion,
    FRATitle,
    GramSabha,
    RightsHolder,
)
from app.db.models import Document, User


GEOMETRY = {
    "type": "MultiPolygon",
    "coordinates": [[[[79.0, 10.0], [79.001, 10.0], [79.001, 10.001], [79.0, 10.001], [79.0, 10.0]]]],
}


class FRADatabaseModelTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_staff_actor_and_rights_holder_are_distinct(self):
        with Session(self.engine) as session:
            staff = User(external_id="staff-1", display_name="Registry staff", role="user")
            holder = RightsHolder(
                display_name="Ramu Naik", holder_type="individual", claimant_category="ST"
            )
            session.add_all([staff, holder])
            session.flush()
            claim = FRAClaim(
                claim_number="FRA-OD-001",
                right_type="IFR",
                status="draft",
                rights_holder_id=holder.id,
                submitted_by=staff.id,
            )
            session.add(claim)
            session.commit()

            self.assertNotEqual(claim.rights_holder_id, claim.submitted_by)
            self.assertEqual(claim.rights_holder.display_name, "Ramu Naik")
            self.assertEqual(claim.submitter.external_id, "staff-1")

    def test_claim_retains_versioned_geometry_and_append_only_decisions(self):
        with Session(self.engine) as session:
            staff = User(external_id="reviewer-1", display_name="Reviewer", role="reviewer")
            gram_sabha = GramSabha(name="Example Gram Sabha", village="Example Village")
            holder = RightsHolder(
                display_name="Example Gram Sabha",
                holder_type="community",
                claimant_category="ST",
                gram_sabha=gram_sabha,
            )
            claim = FRAClaim(
                claim_number="FRA-CFR-001",
                right_type="CFR",
                status="gram_sabha_verified",
                rights_holder=holder,
                gram_sabha=gram_sabha,
                submitter=staff,
            )
            session.add(claim)
            session.flush()
            session.add_all(
                [
                    FRAGeometryVersion(
                        claim_id=claim.id,
                        version=1,
                        geometry=GEOMETRY,
                        source="claimant_sketch",
                        provenance_json={"record": "Form C"},
                        boundary_quality="unverified",
                        created_by=staff.id,
                    ),
                    FRADecision(
                        claim_id=claim.id,
                        authority_level="gram_sabha",
                        from_status="submitted",
                        to_status="gram_sabha_verified",
                        outcome="verified",
                        reasons_json=["Resolution GS-17"],
                        actor_id=staff.id,
                    ),
                ]
            )
            session.commit()

            self.assertEqual(len(claim.geometry_versions), 1)
            self.assertEqual(claim.geometry_versions[0].version, 1)
            self.assertEqual(len(claim.decisions), 1)
            self.assertEqual(claim.decisions[0].reasons_json, ["Resolution GS-17"])

    def test_native_case_links_village_decision_evidence_and_granted_area(self):
        with Session(self.engine) as session:
            staff = User(external_id="domain-reviewer", display_name="Reviewer", role="reviewer")
            village = FRAVillageProfile(
                state_code="TN", state_name="Tamil Nadu", district_code="TN-01",
                district_name="Villupuram", block_code="TN-01-01", block_name="Villupuram",
                village_code="TN-01-01-001", village_name="Arpisampalayam", boundary=GEOMETRY,
                reference_version="tn-v1",
            )
            holder = RightsHolder(display_name="Recorded household", holder_type="household")
            document = Document(
                uploader=staff, storage_key="private/source.pdf", original_filename="source.pdf",
                content_type="application/pdf", sha256="b" * 64, ocr_status="completed",
                idempotency_key="domain-source",
            )
            claim = FRAClaim(
                claim_number="TN-IFR-DOMAIN-1", right_type="IFR", status="granted",
                rights_holder=holder, submitter=staff, village=village,
                document=document, claimed_area_sqm=Decimal("1500.5"),
            )
            geometry = FRAGeometryVersion(
                claim=claim, version=1, geometry=GEOMETRY, source="reviewed_title_map",
                boundary_quality="verified", creator=staff,
            )
            decision = FRADecision(
                claim=claim, authority_level="dlc", from_status="dlc_decided",
                to_status="granted", outcome="granted", reasons_json=["DLC resolution"],
                decision_date=date(2025, 6, 12), reference_number="DLC/2025/42", actor=staff,
            )
            evidence = FRAEvidenceItem(
                claim=claim, category="documentary", source="fra_title", description="Title scan",
                document=document, source_page_start=2, source_page_end=3, creator=staff,
            )
            title = FRATitle(
                claim=claim, version=1, title_number="ROFR-TN-42", geometry_version=geometry,
                granted_area_sqm=Decimal("1250.25"), issuer=staff,
            )
            session.add_all([decision, evidence, title])
            session.commit()

            self.assertEqual(claim.village.village_code, "TN-01-01-001")
            self.assertEqual(village.claims[0].claim_number, "TN-IFR-DOMAIN-1")
            self.assertEqual(decision.reference_number, "DLC/2025/42")
            self.assertEqual((evidence.source_page_start, evidence.source_page_end), (2, 3))
            self.assertEqual(title.granted_area_sqm, Decimal("1250.2500"))

    def test_field_review_preserves_extracted_corrected_and_final_values(self):
        field_review_type = getattr(fra_completion_models, "FRAFieldReview", None)
        self.assertIsNotNone(field_review_type)
        with Session(self.engine) as session:
            reviewer = User(external_id="field-reviewer", display_name="Reviewer", role="reviewer")
            document = Document(
                uploader=reviewer, storage_key="private/archive.png", original_filename="archive.png",
                content_type="image/png", sha256="c" * 64, ocr_status="completed",
                idempotency_key="archive-source",
            )
            batch = FRAImportBatch(
                source_label="District office", state_code="TN", creator=reviewer,
                idempotency_key="domain-batch",
            )
            record = FRAArchiveRecord(
                batch=batch, document=document, legacy_reference="LEGACY-42", state_code="TN",
            )
            model = ModelVersion(
                task="entity_extraction", adapter_type="local_python", name="fra-ner",
                version="1.0.0", registrar=reviewer,
            )
            extraction = FRAExtractionRun(
                archive_record=record, entity_model=model, entity_model_version="1.0.0",
                raw_text="Village: Arpisampalayam", standardized_json={}, field_evidence_json={},
                provenance_json={},
            )
            review = field_review_type(
                extraction_run=extraction, field_name="village", source_page=1,
                source_value_json="Arpisampalayam", extracted_value_json="Arpisampalaym",
                extraction_method="fra_ner", confidence=Decimal("0.72"),
                evidence_json={"start": 9, "end": 24}, corrected_value_json="Arpisampalayam",
                final_value_json="Arpisampalayam", review_state="approved", reviewer=reviewer,
            )
            session.add(review)
            session.commit()

            stored = extraction.field_reviews[0]
            self.assertEqual(stored.source_page, 1)
            self.assertEqual(stored.extracted_value_json, "Arpisampalaym")
            self.assertEqual(stored.corrected_value_json, "Arpisampalayam")
            self.assertEqual(stored.final_value_json, "Arpisampalayam")
            self.assertEqual(stored.reviewer.external_id, "field-reviewer")

    def test_database_rejects_invalid_right_type(self):
        with Session(self.engine) as session:
            staff = User(external_id="constraint-staff", display_name="Staff", role="user")
            holder = RightsHolder(display_name="Holder", holder_type="individual")
            session.add_all([staff, holder]); session.flush()
            session.add(FRAClaim(
                claim_number="INVALID-RIGHT", right_type="PATTA", status="draft",
                rights_holder=holder, submitter=staff,
            ))
            with self.assertRaises(IntegrityError):
                session.commit()


if __name__ == "__main__":
    unittest.main()
