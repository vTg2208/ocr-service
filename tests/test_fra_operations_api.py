import unittest
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import settings
from app.db.base import Base
from app.db.fra_completion_models import ProcessingJob
from app.db.models import User
from app.db.session import get_db
from app.main import app
from app.services.processing_jobs import claim_next_job, enqueue_job, fail_job


class FRAOperationsAPITests(unittest.TestCase):
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
            session.add_all(
                [
                    User(external_id="ops-staff", display_name="Staff", role="user"),
                    User(external_id="ops-reviewer", display_name="Reviewer", role="reviewer"),
                    User(external_id="ops-admin", display_name="Admin", role="admin"),
                ]
            )
            session.commit()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    @staticmethod
    def headers(external_id="ops-staff"):
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
        return {"Authorization": f"Bearer {token}"}

    def test_normal_user_cannot_register_or_activate_models(self):
        payload = {
            "task": "entity_extraction",
            "name": "tn-ner",
            "version": "0.1.0",
            "adapter_type": "manifest",
            "metrics": {"status": "not_evaluated"},
            "configuration": {"ready": True},
        }
        self.assertEqual(
            self.client.post("/api/fra/models", headers=self.headers(), json=payload).status_code,
            403,
        )
        created = self.client.post(
            "/api/fra/models", headers=self.headers("ops-admin"), json=payload
        )
        self.assertEqual(created.status_code, 201, created.text)
        denied = self.client.post(
            f"/api/fra/models/{created.json()['id']}/activate",
            headers=self.headers(),
        )
        self.assertEqual(denied.status_code, 403)
        activated = self.client.post(
            f"/api/fra/models/{created.json()['id']}/activate",
            headers=self.headers("ops-reviewer"),
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(activated.json()["status"], "active")
        self.assertNotIn("artifact_uri", activated.json())

    def test_job_list_is_private_and_reviewer_can_retry_failed_job(self):
        with self.factory() as session:
            staff = session.scalar(select(User).where(User.external_id == "ops-staff"))
            job = enqueue_job(
                session,
                task_type="archive_extract",
                entity_type="archive_record",
                entity_id=uuid.uuid4(),
                actor_id=staff.id,
                idempotency_key="ops-job-1",
                payload={"private_uri": "private://document"},
                max_attempts=1,
            )
            claim_next_job(session, worker_id="test-worker")
            fail_job(session, job, code="provider_down", message="Unavailable", retriable=True)
            session.commit()
            job_id = str(job.id)
        listed = self.client.get("/api/fra/jobs", headers=self.headers())
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn("private://document", listed.text)
        self.assertEqual(listed.json()["items"][0]["state"], "failed")
        denied = self.client.post(
            f"/api/fra/jobs/{job_id}/retry", headers=self.headers()
        )
        self.assertEqual(denied.status_code, 403)
        retried = self.client.post(
            f"/api/fra/jobs/{job_id}/retry", headers=self.headers("ops-reviewer")
        )
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["state"], "queued")

    def test_missing_model_and_job_return_not_found(self):
        missing = "00000000-0000-0000-0000-000000000001"
        self.assertEqual(
            self.client.post(
                f"/api/fra/models/{missing}/activate",
                headers=self.headers("ops-reviewer"),
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/api/fra/jobs/{missing}", headers=self.headers()).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
