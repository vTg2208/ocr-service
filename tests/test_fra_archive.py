import unittest
import uuid

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import FRAArchiveRecord, FRAExtractionRun
from app.db.fra_models import FRAClaim
from app.db.models import Document, User
from app.services.fra_archive import (
    ArchiveConflictError,
    ArchiveValidationError,
    create_archive_record,
    create_import_batch,
    process_archive_extraction,
    promote_archive_record,
    review_archive_record,
    search_archive,
)
from app.services.model_gateway import ManifestFRAEntityExtractor
from app.services.state_profiles import UnsupportedStateError


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

            claim = promote_archive_record(session, record, actor_id=reviewer.id)
            repeated = promote_archive_record(session, record, actor_id=reviewer.id)
            session.commit()

            self.assertEqual(claim.id, repeated.id)
            self.assertEqual(record.promoted_claim_id, claim.id)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(FRAClaim)), 1
            )
            self.assertEqual(claim.document_id, document.id)
            self.assertEqual(claim.provenance_json["archive_record_id"], str(record.id))

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


if __name__ == "__main__":
    unittest.main()
