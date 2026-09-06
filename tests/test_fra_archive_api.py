import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import settings
from app.db.base import Base
from app.db.fra_completion_models import FRAArchiveRecord
from app.db.models import Document, User
from app.db.session import get_db
from app.main import app
from app.services.fra_archive import process_archive_extraction
from app.services.model_gateway import ManifestFRAEntityExtractor


class FRAArchiveAPITests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        def db_override():
            with self.factory() as session:
                yield session

        app.dependency_overrides[get_db] = db_override
        with self.factory() as session:
            staff = User(external_id="archive-staff", display_name="Staff", role="user")
            reviewer = User(
                external_id="archive-reviewer", display_name="Reviewer", role="reviewer"
            )
            session.add_all([staff, reviewer])
            session.flush()
            document = Document(
                uploaded_by=staff.id,
                storage_key="private/archive-api.pdf",
                original_filename="archive-api.pdf",
                content_type="application/pdf",
                sha256="c" * 64,
                idempotency_key="archive-api-doc",
            )
            session.add(document)
            session.commit()
            self.document_id = str(document.id)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    @staticmethod
    def headers(external_id="archive-staff"):
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "sub": external_id,
                "iat": now,
                "exp": now + timedelta(minutes=5),
                "iss": settings.auth_issuer,
                "aud": settings.auth_audience,
            },
            settings.auth_secret,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}", "X-Request-ID": "archive-api-test"}

    def create_batch(self):
        return self.client.post(
            "/api/fra/archive/batches",
            headers=self.headers(),
            json={
                "source_label": "TN synthetic API pack",
                "state": "Tamil Nadu",
                "idempotency_key": "api-batch-1",
                "synthetic": True,
            },
        )

    def create_record(self, batch_id):
        return self.client.post(
            "/api/fra/archive/records",
            headers=self.headers(),
            json={
                "batch_id": batch_id,
                "document_id": self.document_id,
                "legacy_reference": "TN-API-2008-1",
            },
        )

    def test_archive_routes_reject_anonymous_and_unsupported_state(self):
        self.assertEqual(
            self.client.post("/api/fra/archive/batches", json={}).status_code, 401
        )
        response = self.client.post(
            "/api/fra/archive/batches",
            headers=self.headers(),
            json={
                "source_label": "x",
                "state": "Odisha",
                "idempotency_key": "b1",
                "synthetic": True,
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["message"]["code"], "unsupported_state")

    def test_batch_upload_accepts_multipart_files_and_is_idempotent(self):
        class Storage:
            def __init__(self):
                self.items = {}; self.calls = 0

            def put(self, content, suffix):
                self.calls += 1; key = f"private/api-{self.calls}{suffix}"
                self.items[key] = content; return key

            def delete(self, key):
                self.items.pop(key, None)

        storage = Storage()
        request = {
            "headers": {**self.headers(), "Idempotency-Key": "tn-batch-upload-1"},
            "data": {
                "source_office": "District Tribal Welfare Office",
                "district": "Salem",
            },
            "files": [("files", ("TN-IFR-12.pdf", b"%PDF-1.4\n%%EOF", "application/pdf"))],
        }
        with patch("app.api.fra_archive_routes.create_storage", return_value=storage):
            first = self.client.post("/api/fra/archive/batch-upload", **request)
            second = self.client.post("/api/fra/archive/batch-upload", **request)
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(first.json()["accepted"], 1)
        self.assertEqual(first.json()["files"][0]["legacy_reference"], "TN-IFR-12")
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(storage.calls, 1)

    def test_tabular_upload_creates_one_review_record_per_csv_row_and_is_idempotent(self):
        class Storage:
            def __init__(self):
                self.items = {}; self.calls = 0

            def put(self, content, suffix):
                self.calls += 1; key = f"private/table-{self.calls}{suffix}"
                self.items[key] = content; return key

            def delete(self, key):
                self.items.pop(key, None)

        storage = Storage()
        content = (
            "Claim Number,Claimant Name,District,Block,Village,Right Type,Claim Status\n"
            "TN-1,Ramu,Salem,Yercaud,Nagloor,IFR,submitted\n"
            "TN-2,Malli,Salem,Yercaud,Semmanatham,CFR,granted\n"
        ).encode()
        request = {
            "headers": {**self.headers(), "Idempotency-Key": "tn-table-upload-1"},
            "data": {
                "source_office": "District Tribal Welfare Office",
                "district": "Salem",
            },
            "files": {"file": ("legacy.csv", content, "text/csv")},
        }
        with patch("app.api.fra_archive_routes.create_storage", return_value=storage):
            first = self.client.post("/api/fra/archive/tabular-upload", **request)
            second = self.client.post("/api/fra/archive/tabular-upload", **request)

        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(first.json()["accepted"], 2)
        self.assertEqual(first.json()["records"][0]["source_row"], 2)
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(storage.calls, 1)

    def test_archive_list_is_privacy_safe_and_detail_is_role_aware(self):
        batch = self.create_batch()
        self.assertEqual(batch.status_code, 201, batch.text)
        created = self.create_record(batch.json()["id"])
        self.assertEqual(created.status_code, 201, created.text)
        with self.factory() as session:
            record = session.get(FRAArchiveRecord, uuid.UUID(created.json()["id"]))
            staff = session.scalar(select(User).where(User.external_id == "archive-staff"))
            process_archive_extraction(
                session,
                record,
                extractor=ManifestFRAEntityExtractor("tn-api-v1"),
                manifest={
                    "holder_name": "Ramu",
                    "district": "Thanjavur",
                    "block": "Kumbakonam",
                    "village": "Kottur",
                    "right_type": "IFR",
                    "claim_status": "submitted",
                },
                raw_text="Private raw OCR text",
                actor_id=staff.id,
            )
            session.commit()
        listed = self.client.get("/api/fra/archive/records", headers=self.headers())
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["id"], created.json()["id"])
        self.assertNotIn("Private raw OCR text", listed.text)
        self.assertNotIn("private/archive-api.pdf", listed.text)
        normal_detail = self.client.get(
            f"/api/fra/archive/records/{created.json()['id']}", headers=self.headers()
        )
        self.assertNotIn("Private raw OCR text", normal_detail.text)
        self.assertEqual(normal_detail.json()["warning"], "Synthetic sample data")
        self.assertNotIn("demonstration", normal_detail.text.casefold())
        reviewer_detail = self.client.get(
            f"/api/fra/archive/records/{created.json()['id']}",
            headers=self.headers("archive-reviewer"),
        )
        self.assertIn("Private raw OCR text", reviewer_detail.text)
        self.assertNotIn("private/archive-api.pdf", reviewer_detail.text)
        field_reviews = reviewer_detail.json()["extraction_runs"][0]["field_reviews"]
        self.assertTrue(any(item["field_name"] == "holder_name" for item in field_reviews))
        self.assertEqual(reviewer_detail.json()["source_document"]["filename"], "archive-api.pdf")
        holder_chain = next(
            item["evidence_chain"] for item in field_reviews if item["field_name"] == "holder_name"
        )
        self.assertEqual(holder_chain["source"], "TN synthetic API pack")
        self.assertEqual(holder_chain["document"]["filename"], "archive-api.pdf")
        self.assertEqual(holder_chain["value"], "Ramu")
        self.assertEqual(holder_chain["extraction"]["model_version"], "tn-api-v1")
        self.assertIn("page", holder_chain["locator"])
        self.assertIn("reviewer", holder_chain)
        self.assertIn("final_value", holder_chain)
        self.assertNotIn("field_reviews", normal_detail.json()["extraction_runs"][0])

        with self.factory() as session:
            session.add(User(external_id="archive-other", display_name="Other", role="user"))
            session.commit()
        hidden_list = self.client.get(
            "/api/fra/archive/records", headers=self.headers("archive-other")
        )
        self.assertEqual(hidden_list.json()["items"], [])
        hidden_detail = self.client.get(
            f"/api/fra/archive/records/{created.json()['id']}",
            headers=self.headers("archive-other"),
        )
        self.assertEqual(hidden_detail.status_code, 404)

    def test_review_requires_reviewer_and_promotion_is_idempotent(self):
        batch_id = self.create_batch().json()["id"]
        record_id = self.create_record(batch_id).json()["id"]
        with self.factory() as session:
            record = session.get(FRAArchiveRecord, uuid.UUID(record_id))
            staff = session.scalar(select(User).where(User.external_id == "archive-staff"))
            run = process_archive_extraction(
                session,
                record,
                extractor=ManifestFRAEntityExtractor("tn-api-v1"),
                manifest={
                    "holder_name": "Ramu",
                    "district": "Thanjavur",
                    "block": "Kumbakonam",
                    "village": "Kottur",
                    "right_type": "IFR",
                    "claim_status": "submitted",
                    "claim_number": "TN-API-IFR-1",
                },
                actor_id=staff.id,
            )
            fields = run.standardized_json
            session.commit()
        denied = self.client.post(
            f"/api/fra/archive/records/{record_id}/review",
            headers=self.headers(),
            json={"expected_revision": 0, "reviewed_fields": fields},
        )
        self.assertEqual(denied.status_code, 403)
        reviewed = self.client.post(
            f"/api/fra/archive/records/{record_id}/review",
            headers=self.headers("archive-reviewer"),
            json={"expected_revision": 0, "reviewed_fields": fields},
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        missing = self.client.post(
            f"/api/fra/archive/records/{record_id}/promote", headers=self.headers("archive-reviewer"))
        self.assertEqual(missing.status_code, 422, missing.text)
        first = self.client.post(
            f"/api/fra/archive/records/{record_id}/promote",
            headers=self.headers("archive-reviewer"),
            json={"expected_revision": 1},
        )
        stale = self.client.post(
            f"/api/fra/archive/records/{record_id}/promote", headers=self.headers("archive-reviewer"),
            json={"expected_revision": 1})
        self.assertEqual(stale.status_code, 409, stale.text)
        second = self.client.post(
            f"/api/fra/archive/records/{record_id}/promote",
            headers=self.headers("archive-reviewer"),
            json={"expected_revision": 2},
        )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()["claim_id"], second.json()["claim_id"])

    def test_reject_extraction_requires_reviewer_and_reason(self):
        batch_id = self.create_batch().json()["id"]
        record_id = self.create_record(batch_id).json()["id"]
        with self.factory() as session:
            record = session.get(FRAArchiveRecord, uuid.UUID(record_id))
            staff = session.scalar(select(User).where(User.external_id == "archive-staff"))
            process_archive_extraction(
                session, record, extractor=ManifestFRAEntityExtractor("tn-api-v1"),
                manifest={"holder_name": "Ramu"}, actor_id=staff.id,
            )
            session.commit()
        denied = self.client.post(
            f"/api/fra/archive/records/{record_id}/reject", headers=self.headers(),
            json={"expected_revision": 0, "reason": "Not an FRA record"},
        )
        self.assertEqual(denied.status_code, 403)
        missing = self.client.post(
            f"/api/fra/archive/records/{record_id}/reject",
            headers=self.headers("archive-reviewer"), json={"expected_revision": 0, "reason": ""},
        )
        self.assertEqual(missing.status_code, 422)
        rejected = self.client.post(
            f"/api/fra/archive/records/{record_id}/reject",
            headers=self.headers("archive-reviewer"),
            json={"expected_revision": 0, "reason": "Not an FRA record"},
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()["review_state"], "rejected")

    def test_promotion_failure_returns_conflict_and_rolls_back_created_holder(self):
        from sqlalchemy import func
        from app.db.fra_models import FRAClaim, RightsHolder
        from app.db.models import AuditEvent
        from app.services.fra_archive import review_archive_record
        batch_id = self.create_batch().json()["id"]
        record_id = self.create_record(batch_id).json()["id"]
        with self.factory() as setup:
            reviewer = setup.scalar(select(User).where(User.external_id == "archive-reviewer"))
            record = setup.get(FRAArchiveRecord, uuid.UUID(record_id))
            record.review_state = "needs_review"
            setup.flush()
            review_archive_record(setup, record, expected_revision=0, reviewer_id=reviewer.id,
                reviewed_fields={"holder_name": "Ramu", "district": "Thanjavur", "block": "Kumbakonam",
                                 "village": "Kottur", "right_type": "IFR", "claim_status": "submitted",
                                 "claim_number": "DUPLICATE"})
            holder = RightsHolder(display_name="Existing", holder_type="individual")
            setup.add(FRAClaim(claim_number="DUPLICATE", right_type="IFR", status="draft",
                               rights_holder=holder, submitted_by=reviewer.id))
            setup.commit()
        response = self.client.post(f"/api/fra/archive/records/{record_id}/promote",
            headers=self.headers("archive-reviewer"), json={"expected_revision": 1})
        self.assertEqual(response.status_code, 409, response.text)
        with self.factory() as check:
            self.assertEqual(check.scalar(select(func.count()).select_from(RightsHolder)), 1)
            self.assertEqual(check.scalar(select(func.count()).select_from(FRAClaim)), 1)
            row = check.get(FRAArchiveRecord, uuid.UUID(record_id))
            self.assertEqual((row.revision, row.review_state, row.promoted_claim_id), (1, "reviewed", None))
            self.assertEqual(check.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.action == "fra_archive_record_promoted")), 0)


if __name__ == "__main__":
    unittest.main()
