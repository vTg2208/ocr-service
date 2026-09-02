import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.fra_completion_models import FRAArchiveRecord, ProcessingJob
from app.db.models import Document, User
from app.services.fra_document_intake import ArchiveUpload, ingest_archive_batch
from app.services.malware import MalwareDetectedError


PDF_ONE = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"
PDF_TWO = b"%PDF-1.4\n2 0 obj\n<<>>\nendobj\n%%EOF"


class MemoryStorage:
    def __init__(self):
        self.values = {}
        self.deleted = []
        self.put_calls = 0

    def put(self, content, suffix):
        self.put_calls += 1
        key = f"private/archive-{self.put_calls}{suffix}"
        self.values[key] = content
        return key

    def delete(self, key):
        self.deleted.append(key)
        self.values.pop(key, None)


class SelectiveScanner:
    def scan(self, content):
        if b"MALWARE" in content:
            raise MalwareDetectedError("Uploaded file failed malware scanning.")


class FRADocumentIntakeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory() as session:
            user = User(external_id="archive-uploader", display_name="Uploader", role="user")
            session.add(user); session.commit(); self.user_id = user.id

    def tearDown(self):
        self.engine.dispose()

    def ingest(self, session, storage, files, *, key="batch-1", enqueue=None):
        options = {}
        if enqueue is not None:
            options["enqueue"] = enqueue
        return ingest_archive_batch(
            session,
            files=files,
            source_office="District Tribal Welfare Office",
            district="Salem",
            actor_id=self.user_id,
            idempotency_key=key,
            storage=storage,
            scanner=SelectiveScanner(),
            request_id="document-intake-test",
            **options,
        )

    def test_mixed_batch_accepts_valid_files_and_reports_validation_errors(self):
        storage = MemoryStorage()
        with self.factory() as session:
            result = self.ingest(session, storage, [
                ArchiveUpload("TN-IFR-001.pdf", "application/pdf", PDF_ONE),
                ArchiveUpload("broken.pdf", "application/pdf", b"not a document"),
            ])
            session.commit()
            self.assertEqual((result["accepted"], result["rejected"]), (1, 1))
            self.assertEqual(result["files"][0]["status"], "accepted")
            self.assertEqual(result["files"][1]["error_code"], "invalid_file")
            self.assertEqual(session.scalar(select(func.count(Document.id))), 1)
            self.assertEqual(session.scalar(select(func.count(FRAArchiveRecord.id))), 1)
            self.assertEqual(session.scalar(select(func.count(ProcessingJob.id))), 1)
            self.assertEqual(result["batch_status"], "partial")

    def test_duplicate_checksum_is_rejected_before_a_second_storage_write(self):
        storage = MemoryStorage()
        with self.factory() as session:
            result = self.ingest(session, storage, [
                ArchiveUpload("first.pdf", "application/pdf", PDF_ONE),
                ArchiveUpload("copy.pdf", "application/pdf", PDF_ONE),
            ])
            session.commit()
            self.assertEqual((result["accepted"], result["rejected"]), (1, 1))
            self.assertEqual(result["files"][1]["error_code"], "duplicate_file")
            self.assertEqual(storage.put_calls, 1)

    def test_malware_failure_is_isolated_and_never_persisted(self):
        storage = MemoryStorage()
        infected = b"%PDF-1.4\nMALWARE\n%%EOF"
        with self.factory() as session:
            result = self.ingest(session, storage, [
                ArchiveUpload("infected.pdf", "application/pdf", infected),
                ArchiveUpload("clean.pdf", "application/pdf", PDF_TWO),
            ])
            session.commit()
            self.assertEqual((result["accepted"], result["rejected"]), (1, 1))
            self.assertEqual(result["files"][0]["error_code"], "malware_detected")
            self.assertEqual(storage.put_calls, 1)

    def test_idempotent_replay_returns_existing_records_without_storing_again(self):
        storage = MemoryStorage()
        with self.factory() as session:
            first = self.ingest(
                session, storage, [ArchiveUpload("first.pdf", "application/pdf", PDF_ONE)]
            )
            session.commit()
            second = self.ingest(
                session, storage, [ArchiveUpload("changed.pdf", "application/pdf", PDF_TWO)]
            )
            session.commit()
            self.assertFalse(first["replayed"])
            self.assertTrue(second["replayed"])
            self.assertEqual(second["batch_id"], first["batch_id"])
            self.assertEqual(second["accepted"], 1)
            self.assertEqual(storage.put_calls, 1)

    def test_storage_is_cleaned_and_database_savepoint_rolls_back_on_setup_failure(self):
        storage = MemoryStorage()

        def fail_enqueue(*_args, **_kwargs):
            raise RuntimeError("queue unavailable")

        with self.factory() as session:
            result = self.ingest(
                session,
                storage,
                [ArchiveUpload("first.pdf", "application/pdf", PDF_ONE)],
                enqueue=fail_enqueue,
            )
            session.commit()
            self.assertEqual((result["accepted"], result["rejected"]), (0, 1))
            self.assertEqual(result["files"][0]["error_code"], "processing_setup_failed")
            self.assertEqual(len(storage.deleted), 1)
            self.assertEqual(session.scalar(select(func.count(Document.id))), 0)
            self.assertEqual(session.scalar(select(func.count(FRAArchiveRecord.id))), 0)


if __name__ == "__main__":
    unittest.main()
