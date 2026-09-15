import unittest
import uuid

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import FRAArchiveRecord, FRAExtractionRun, FRAVillageProfile
from app.db.fra_models import FRAClaim, RightsHolder
from app.db.models import Document, Parcel, User
from app.services.fra_archive import (
    ArchiveConflictError,
    ArchiveValidationError,
    create_archive_record,
    create_import_batch,
    process_archive_extraction,
    promote_archive_record,
    reject_archive_record,
    review_archive_record,
    search_archive,
)
from app.services.model_gateway import ManifestFRAEntityExtractor
from app.services.state_profiles import UnsupportedStateError


GEOMETRY = {
    "type": "MultiPolygon",
    "coordinates": [[[[79.0, 10.0], [79.01, 10.0], [79.01, 10.01], [79.0, 10.01], [79.0, 10.0]]]],
}


class FRAArchiveTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.extractor = ManifestFRAEntityExtractor("tn-manifest-v1")

    def tearDown(self):
        self.engine.dispose()

    def _actors_and_document(self, session):
        staff = User(external_id=f"staff-{uuid.uuid4()}", display_name="Staff", role="user")
        reviewer = User(
            external_id=f"reviewer-{uuid.uuid4()}", display_name="Reviewer", role="reviewer"
        )
        session.add_all([staff, reviewer])
        session.flush()
        document = Document(
            uploaded_by=staff.id,
            storage_key=f"private/{uuid.uuid4()}.pdf",
            original_filename="synthetic-fra.pdf",
            content_type="application/pdf",
            sha256="b" * 64,
            idempotency_key=f"doc-{uuid.uuid4()}",
        )
        session.add(document)
        session.flush()
        return staff, reviewer, document

    def _record(self, session, staff, document, *, key="batch-1", reference="TN-2008-1"):
        batch = create_import_batch(
            session,
            source_label="TN synthetic",
            state="Tamil Nadu",
            actor_id=staff.id,
            idempotency_key=key,
            synthetic=True,
            provenance={"source": "final-year-project-demo", "synthetic": True},
        )
        return create_archive_record(
            session,
            batch=batch,
            document_id=document.id,
            legacy_reference=reference,
            actor_id=staff.id,
        )

    def _extract(self, session, record, staff, **overrides):
        manifest = {
            "holder_name": "Ramu",
            "district": "Thanjavur",
            "block": "Kumbakonam",
            "village": "Kottur",
            "right_type": "IFR",
            "claim_status": "submitted",
            "claim_number": "TN-IFR-2008-1",
            "claim_year": 2008,
            "confidence": 0.84,
            **overrides,
        }
        return process_archive_extraction(
            session,
            record,
            extractor=self.extractor,
            manifest=manifest,
            raw_text="Synthetic Form A",
            actor_id=staff.id,
        )

    def test_tamil_nadu_archive_record_is_searchable_after_review(self):
        with Session(self.engine) as session:
            staff, reviewer, document = self._actors_and_document(session)
            record = self._record(session, staff, document)
            self._extract(session, record, staff)
            review_archive_record(
                session,
                record,
                reviewed_fields=record.latest_extraction.standardized_json,
                reviewer_id=reviewer.id,
                expected_revision=0,
            )
            session.commit()

            results = search_archive(
                session,
                query="Ramu Kottur",
                filters={"district": "Thanjavur", "right_type": "IFR"},
            )
            self.assertEqual([item.id for item in results], [record.id])
            self.assertEqual(record.review_state, "reviewed")
            self.assertEqual(record.revision, 1)

    def test_extraction_runs_are_versioned_without_overwrite(self):
        with Session(self.engine) as session:
            staff, _reviewer, document = self._actors_and_document(session)
            record = self._record(session, staff, document)
            first = self._extract(session, record, staff, holder_name="Ramu")
            second = self._extract(session, record, staff, holder_name="Ramu Corrected")
            session.commit()

            self.assertNotEqual(first.id, second.id)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(FRAExtractionRun)), 2
            )
            self.assertEqual(record.latest_extraction.standardized_json["holder_name"], "Ramu Corrected")

    def test_stale_review_does_not_mutate_record(self):
        with Session(self.engine) as session:
            staff, reviewer, document = self._actors_and_document(session)
            record = self._record(session, staff, document)
            self._extract(session, record, staff)

            with self.assertRaisesRegex(ArchiveConflictError, "changed since"):
                review_archive_record(
                    session,
                    record,
                    reviewed_fields=record.latest_extraction.standardized_json,
                    reviewer_id=reviewer.id,
                    expected_revision=99,
                )

            self.assertEqual(record.review_state, "needs_review")
            self.assertEqual(record.revision, 0)

    def test_reviewer_can_reject_extraction_with_a_recorded_reason(self):
        with Session(self.engine) as session:
            staff, reviewer, document = self._actors_and_document(session)
            record = self._record(session, staff, document)
            self._extract(session, record, staff)

            reject_archive_record(
                session, record, reason="The scan does not contain an FRA claim.",
                reviewer_id=reviewer.id, expected_revision=0,
            )

            self.assertEqual(record.review_state, "rejected")
            self.assertEqual(record.revision, 1)
            self.assertEqual(record.provenance_json["extraction_rejection"]["reason"],
                             "The scan does not contain an FRA claim.")
            self.assertTrue(all(
                field.review_state == "rejected" for field in record.latest_extraction.field_reviews
            ))

    def test_unsupported_state_and_synthetic_mismatch_are_explicit(self):
        with Session(self.engine) as session:
            staff, _reviewer, document = self._actors_and_document(session)
            with self.assertRaises(UnsupportedStateError):
                create_import_batch(
                    session,
                    source_label="Unsupported",
                    state="Odisha",
                    actor_id=staff.id,
                    idempotency_key="unsupported",
                    synthetic=True,
                    provenance={"source": "test", "synthetic": True},
                )
            record = self._record(session, staff, document)
            with self.assertRaisesRegex(ArchiveValidationError, "synthetic flag"):
                create_archive_record(
                    session,
                    batch=record.batch,
                    document_id=document.id,
                    legacy_reference="TN-MISMATCH",
                    actor_id=staff.id,
                    synthetic=False,
                )

    def test_batch_record_and_promotion_are_idempotent(self):
        with Session(self.engine) as session:
            staff, reviewer, document = self._actors_and_document(session)
            record = self._record(session, staff, document)
            duplicate = self._record(session, staff, document)
            self.assertEqual(duplicate.id, record.id)
            self._extract(session, record, staff)
            review_archive_record(
                session,
                record,
                reviewed_fields=record.latest_extraction.standardized_json,
                reviewer_id=reviewer.id,
                expected_revision=0,
            )

            claim = promote_archive_record(session, record, expected_revision=record.revision, actor_id=reviewer.id)
            repeated = promote_archive_record(session, record, expected_revision=record.revision, actor_id=reviewer.id)
            session.commit()

            self.assertEqual(claim.id, repeated.id)
            self.assertEqual(record.promoted_claim_id, claim.id)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(FRAClaim)), 1
            )
            self.assertEqual(claim.document_id, document.id)
            self.assertEqual(claim.provenance_json["archive_record_id"], str(record.id))

    def test_reviewed_legacy_record_maps_canonical_fra_entities_and_history(self):
        with Session(self.engine) as session:
            staff, reviewer, document = self._actors_and_document(session)
            village = FRAVillageProfile(
                state_code="TN", state_name="Tamil Nadu", district_code="TN-13",
                district_name="Thanjavur", block_code="TN-13-01", block_name="Kumbakonam",
                village_code="TN-13-01-001", village_name="Kottur", boundary=GEOMETRY,
                tribal_groups_json=[], socioeconomic_json={}, provenance_json={"source": "lgd"},
                reference_version="lgd-2025", synthetic=True,
            )
            holder = RightsHolder(
                display_name="Ramu", holder_type="individual",
                metadata_json={"location_key": "tn|thanjavur|kumbakonam|kottur"},
            )
            parcel = Parcel(
                state="Tamil Nadu", district="Thanjavur", taluk="Kumbakonam",
                village="Kottur", survey_number="614", subdivision_number="1B",
                official_area_sqm=12500, geometry=GEOMETRY, source="reviewed-cadastral",
                source_version="2025", source_record_id="614-1B", boundary_quality="surveyed",
            )
            session.add_all([village, holder, parcel]); session.flush()
            record = self._record(session, staff, document)
            self._extract(
                session, record, staff, claim_status="Granted", survey_number="614",
                subdivision_number="1B", claimed_area="1.25 hectares",
                granted_area="1.10 hectares", decision_date="14/11/2025",
                decision_authority="District Level Committee", title_number="TN-ROFR-44",
            )
            review_archive_record(
                session, record, reviewed_fields=record.latest_extraction.standardized_json,
                reviewer_id=reviewer.id, expected_revision=0,
            )

            claim = promote_archive_record(
                session, record, expected_revision=record.revision, actor_id=reviewer.id,
            )
            session.flush()

            self.assertEqual(claim.village_id, village.id)
            self.assertEqual(claim.rights_holder_id, holder.id)
            self.assertEqual(claim.parcel_id, parcel.id)
            self.assertEqual(float(claim.claimed_area_sqm), 12500.0)
            self.assertEqual(claim.status, "granted")
            self.assertEqual(claim.decisions[0].authority_level, "District Level Committee")
            self.assertEqual(claim.decisions[0].decision_date.isoformat(), "2025-11-14")
            self.assertEqual(claim.titles[0].title_number, "TN-ROFR-44")
            self.assertEqual(float(claim.titles[0].granted_area_sqm), 11000.0)
            self.assertEqual(claim.geometry_versions[0].source, "reviewed_cadastral_match")
            self.assertEqual(claim.provenance_json["legacy_mapping"]["village_match"], "exact")
            self.assertEqual(claim.provenance_json["legacy_mapping"]["parcel_match"], "matched")

    def test_duplicate_reviewed_legacy_record_cannot_create_second_native_claim(self):
        with Session(self.engine) as session:
            staff, reviewer, first_document = self._actors_and_document(session)
            second_document = Document(
                uploaded_by=staff.id, storage_key=f"private/{uuid.uuid4()}.pdf",
                original_filename="duplicate-fra.pdf", content_type="application/pdf",
                sha256="c" * 64, idempotency_key=f"doc-{uuid.uuid4()}",
            )
            session.add(second_document); session.flush()
            first = self._record(session, staff, first_document, key="first", reference="LEG-1")
            second = self._record(session, staff, second_document, key="second", reference="LEG-2")
            for record in (first, second):
                self._extract(session, record, staff)
                review_archive_record(
                    session, record, reviewed_fields=record.latest_extraction.standardized_json,
                    reviewer_id=reviewer.id, expected_revision=0,
                )
            promote_archive_record(
                session, first, expected_revision=first.revision, actor_id=reviewer.id,
            )
            with self.assertRaisesRegex(ArchiveConflictError, "duplicate reviewed legacy record"):
                promote_archive_record(
                    session, second, expected_revision=second.revision, actor_id=reviewer.id,
                )

    def test_invalid_review_fields_do_not_partially_apply(self):
        with Session(self.engine) as session:
            staff, reviewer, document = self._actors_and_document(session)
            record = self._record(session, staff, document)
            self._extract(session, record, staff)
            fields = dict(record.latest_extraction.standardized_json)
            fields["right_type"] = "patta"

            with self.assertRaisesRegex(ArchiveValidationError, "IFR, CR, or CFR"):
                review_archive_record(
                    session,
                    record,
                    reviewed_fields=fields,
                    reviewer_id=reviewer.id,
                    expected_revision=0,
                )

            self.assertEqual(record.reviewed_fields_json, {})
            self.assertEqual(record.review_state, "needs_review")

    def test_two_sessions_cannot_review_the_same_revision(self):
        from app.db.models import AuditEvent
        with Session(self.engine) as setup:
            staff, reviewer, document = self._actors_and_document(setup)
            record = self._record(setup, staff, document)
            run = self._extract(setup, record, staff)
            record_id, actor_id, fields = record.id, reviewer.id, dict(run.standardized_json)
            setup.commit()
        with Session(self.engine) as first, Session(self.engine) as second:
            current = first.get(FRAArchiveRecord, record_id)
            stale = second.get(FRAArchiveRecord, record_id)
            review_archive_record(first, current, reviewed_fields=fields, reviewer_id=actor_id, expected_revision=0)
            first.commit()
            with self.assertRaises(ArchiveConflictError):
                review_archive_record(second, stale, reviewed_fields={**fields, "holder_name": "Wrong holder"},
                                      reviewer_id=actor_id, expected_revision=0)
            second.rollback()
        with Session(self.engine) as check:
            self.assertEqual(check.get(FRAArchiveRecord, record_id).holder_display_name, "Ramu")
            self.assertEqual(check.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.action == "fra_archive_record_reviewed")), 1)

    def _committed_reviewed_record(self):
        with Session(self.engine) as setup:
            staff, reviewer, document = self._actors_and_document(setup)
            record = self._record(setup, staff, document)
            run = self._extract(setup, record, staff)
            fields = dict(run.standardized_json)
            review_archive_record(setup, record, reviewed_fields=fields, reviewer_id=reviewer.id, expected_revision=0)
            result = record.id, reviewer.id, staff.id, fields
            setup.commit()
            return result

    def test_concurrent_review_invalidates_promotion(self):
        from app.db.fra_models import RightsHolder
        from app.db.models import AuditEvent
        record_id, reviewer_id, staff_id, fields = self._committed_reviewed_record()
        with Session(self.engine) as first, Session(self.engine) as second:
            current, stale = first.get(FRAArchiveRecord, record_id), second.get(FRAArchiveRecord, record_id)
            review_archive_record(first, current, reviewed_fields={**fields, "holder_name": "Corrected holder"},
                                  reviewer_id=reviewer_id, expected_revision=1)
            first.commit()
            with self.assertRaises(ArchiveConflictError):
                promote_archive_record(second, stale, expected_revision=1, actor_id=reviewer_id)
            second.rollback()
        with Session(self.engine) as check:
            self.assertEqual(check.scalar(select(func.count()).select_from(FRAClaim)), 0)
            self.assertEqual(check.scalar(select(func.count()).select_from(RightsHolder)), 0)
            self.assertEqual(check.get(FRAArchiveRecord, record_id).holder_display_name, "Corrected holder")
            self.assertEqual(check.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.action == "fra_archive_record_promoted")), 0)

    def test_concurrent_promotion_invalidates_review_and_duplicate_promotion(self):
        from app.db.fra_models import RightsHolder
        record_id, reviewer_id, staff_id, fields = self._committed_reviewed_record()
        with Session(self.engine) as first, Session(self.engine) as second, Session(self.engine) as third:
            current, stale = first.get(FRAArchiveRecord, record_id), second.get(FRAArchiveRecord, record_id)
            stale_promotion = third.get(FRAArchiveRecord, record_id)
            promote_archive_record(first, current, expected_revision=1, actor_id=reviewer_id)
            first.commit()
            with self.assertRaises(ArchiveConflictError):
                promote_archive_record(third, stale_promotion, expected_revision=1, actor_id=reviewer_id)
            third.rollback()
            with self.assertRaises(ArchiveConflictError):
                review_archive_record(second, stale, reviewed_fields=fields, reviewer_id=reviewer_id, expected_revision=1)
            second.rollback()
        with Session(self.engine) as check:
            self.assertEqual(check.get(FRAArchiveRecord, record_id).review_state, "promoted")
            self.assertEqual(check.scalar(select(func.count()).select_from(FRAClaim)), 1)
            self.assertEqual(check.scalar(select(func.count()).select_from(RightsHolder)), 1)

    def test_extraction_rerun_invalidates_loaded_review(self):
        record_id, reviewer_id, staff_id, fields = self._committed_reviewed_record()
        with Session(self.engine) as first, Session(self.engine) as second:
            current, stale = first.get(FRAArchiveRecord, record_id), second.get(FRAArchiveRecord, record_id)
            staff = first.get(User, staff_id)
            self._extract(first, current, staff, holder_name="New source holder")
            first.commit()
            with self.assertRaises(ArchiveConflictError):
                review_archive_record(second, stale, reviewed_fields=fields, reviewer_id=reviewer_id, expected_revision=1)
            second.rollback()
        with Session(self.engine) as check:
            row = check.get(FRAArchiveRecord, record_id)
            self.assertEqual(row.review_state, "needs_review")
            self.assertEqual(row.latest_extraction.standardized_json["holder_name"], "New source holder")
            self.assertEqual(len(row.extraction_runs), 2)

    def test_rerun_while_needs_review_invalidates_same_state_token(self):
        with Session(self.engine) as setup:
            staff, reviewer, document = self._actors_and_document(setup)
            record = self._record(setup, staff, document)
            run = self._extract(setup, record, staff)
            record_id, reviewer_id, staff_id, fields = record.id, reviewer.id, staff.id, dict(run.standardized_json)
            setup.commit()
        with Session(self.engine) as first, Session(self.engine) as second:
            current, stale = first.get(FRAArchiveRecord, record_id), second.get(FRAArchiveRecord, record_id)
            self._extract(first, current, first.get(User, staff_id), holder_name="Updated source")
            first.commit()
            with self.assertRaises(ArchiveConflictError):
                review_archive_record(second, stale, reviewed_fields=fields, reviewer_id=reviewer_id, expected_revision=0)
            second.rollback()
        with Session(self.engine) as check:
            row = check.get(FRAArchiveRecord, record_id)
            self.assertEqual(row.review_state, "needs_review")
            self.assertEqual(row.revision, 1)
            self.assertEqual(len(row.extraction_runs), 2)

    def test_extraction_rerun_invalidates_loaded_promotion(self):
        record_id, reviewer_id, staff_id, fields = self._committed_reviewed_record()
        with Session(self.engine) as first, Session(self.engine) as second:
            current, stale = first.get(FRAArchiveRecord, record_id), second.get(FRAArchiveRecord, record_id)
            self._extract(first, current, first.get(User, staff_id), holder_name="Changed extraction")
            first.commit()
            with self.assertRaises(ArchiveConflictError):
                promote_archive_record(second, stale, expected_revision=1, actor_id=reviewer_id)
            second.rollback()
        with Session(self.engine) as check:
            self.assertEqual(check.scalar(select(func.count()).select_from(FRAClaim)), 0)
            self.assertEqual(check.get(FRAArchiveRecord, record_id).review_state, "needs_review")


if __name__ == "__main__":
    unittest.main()
