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
        first = self.client.post(
            f"/api/fra/archive/records/{record_id}/promote",
            headers=self.headers("archive-reviewer"),
        )
        second = self.client.post(
            f"/api/fra/archive/records/{record_id}/promote",
            headers=self.headers("archive-reviewer"),
        )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()["claim_id"], second.json()["claim_id"])


if __name__ == "__main__":
    unittest.main()
