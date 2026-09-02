import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.fra_completion_models import FRAArchiveRecord, FRAExtractionRun, ModelVersion
from app.db.models import Document, User
from app.services.fra_archive import create_archive_record, create_import_batch
from app.services.fra_job_handlers import get_job_handler
from app.services.processing_jobs import JobExecutionError, enqueue_job


class FRAArchiveJobHandlerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def build_job(self, session, *, synthetic=False, manifest=None):
        staff = User(external_id=str(uuid.uuid4()), display_name="TN staff", role="user")
        session.add(staff); session.flush()
        document = Document(
            uploaded_by=staff.id,
            storage_key=f"private/{uuid.uuid4()}.pdf",
            original_filename="TN-IFR-77.pdf",
            content_type="application/pdf",
            sha256=uuid.uuid4().hex * 2,
            ocr_status="queued",
            idempotency_key=str(uuid.uuid4()),
        )
        session.add(document); session.flush()
        batch = create_import_batch(
            session,
            source_label="TN archive",
            state="Tamil Nadu",
            actor_id=staff.id,
            idempotency_key=str(uuid.uuid4()),
            synthetic=synthetic,
            provenance={"source": "test", "synthetic": synthetic},
        )
        record = create_archive_record(
            session,
            batch=batch,
            document_id=document.id,
            legacy_reference="TN-IFR-77",
            actor_id=staff.id,
            synthetic=synthetic,
        )
        job = enqueue_job(
            session,
            task_type="archive_extract",
            entity_type="archive_record",
            entity_id=record.id,
            actor_id=staff.id,
            idempotency_key=str(uuid.uuid4()),
            payload={"record_id": str(record.id), "manifest": manifest or {}},
        )
        return staff, document, record, job

    def test_non_synthetic_archive_job_reads_stored_document_and_uses_active_adapter(self):
        with self.factory() as session:
            staff, document, record, job = self.build_job(session)
            model = ModelVersion(
                task="entity_extraction",
                adapter_type="local_python",
                name="tn-fra-ner",
                version="1.0.0",
                status="active",
                configuration_json={"ready": True, "runner": "tamil_nadu_fra_regex_v1"},
                label_map_json={}, metrics_json={"status": "evaluated"}, registered_by=staff.id,
            )
            session.add(model); session.flush()
            with patch(
                "app.services.fra_job_handlers._read_archive_document",
                return_value=b"stored bytes",
            ), patch(
                "app.services.fra_job_handlers._recognize_archive_document",
                return_value=(
                    "Claim No: TN-IFR-77\nClaimant: Ramu\nDistrict: Salem\n"
                    "Block: Yercaud\nVillage: Kottur\nRight Type: IFR\nStatus: Pending",
                    0.91,
                    "paddle-ta-v1",
                    25,
                ),
            ):
                result = get_job_handler("archive_extract")(session, job)
            run = session.get(FRAExtractionRun, uuid.UUID(result["extraction_run_id"]))
            self.assertEqual(run.standardized_json["claim_number"], "TN-IFR-77")
            self.assertEqual(run.entity_model_version_id, model.id)
            self.assertEqual(run.ocr_model_version, "paddle-ta-v1")
            self.assertEqual(document.ocr_status, "completed")
            self.assertNotIn("stored bytes", str(run.provenance_json))

    def test_non_synthetic_archive_job_waits_for_an_active_model(self):
        with self.factory() as session:
            _staff, _document, _record, job = self.build_job(session)
            with self.assertRaisesRegex(JobExecutionError, "No active FRA entity model") as raised:
                get_job_handler("archive_extract")(session, job)
            self.assertTrue(raised.exception.retriable)

    def test_synthetic_archive_job_keeps_explicit_manifest_replay(self):
        with self.factory() as session:
            _staff, _document, record, job = self.build_job(
                session,
                synthetic=True,
                manifest={"village": "Kottur", "district": "Salem", "right_type": "IFR"},
            )
            result = get_job_handler("archive_extract")(session, job)
            run = session.get(FRAExtractionRun, uuid.UUID(result["extraction_run_id"]))
            self.assertEqual(run.standardized_json["village"], "Kottur")
            self.assertTrue(run.provenance_json["synthetic"])
            self.assertEqual(record.review_state, "needs_review")


if __name__ == "__main__":
    unittest.main()
