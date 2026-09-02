import unittest
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import settings
from app.db.base import Base
from app.db.fra_models import RightsHolder
from app.db.models import Claim, Document, Parcel, User
from app.db.session import get_db
from app.main import app
from app.services.fra_intake import ensure_intake_for_legacy_claim


GEOMETRY = {
    "type": "MultiPolygon",
    "coordinates": [[[[78.0, 11.0], [78.01, 11.0], [78.01, 11.01], [78.0, 11.01], [78.0, 11.0]]]],
}


class FRAIntakeAPITests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        def db_override():
            with self.factory() as session:
                yield session

        app.dependency_overrides[get_db] = db_override
        with self.factory() as session:
            user = User(external_id="intake-user", role="user")
            reviewer = User(external_id="intake-api-reviewer", role="reviewer")
            parcel = Parcel(
                state="Tamil Nadu", district="Salem", taluk="Yercaud", village="Kottur",
                survey_number="12", subdivision_number="A", geometry=GEOMETRY,
                source="test", source_version="v1", source_record_id="api-p-1",
            )
            session.add_all([user, reviewer, parcel]); session.flush()
            document = Document(
                uploaded_by=user.id, storage_key="private/intake-api.pdf",
                original_filename="intake-api.pdf", content_type="application/pdf",
                sha256="c" * 64, idempotency_key="intake-api-doc",
            )
            session.add(document); session.flush()
            legacy = Claim(
                claimant_id=user.id, parcel_id=parcel.id, document_id=document.id,
                match_method="exact", idempotency_key="legacy-api",
            )
            holder = RightsHolder(
                display_name="Ramu", holder_type="individual", external_reference="api-holder",
            )
            session.add_all([legacy, holder]); session.flush()
            intake = ensure_intake_for_legacy_claim(session, legacy, actor_id=user.id)
            session.commit()
            self.intake_id, self.holder_id = str(intake.id), str(holder.id)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); app.dependency_overrides.clear(); self.engine.dispose()

    @staticmethod
    def headers(external_id):
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {"sub": external_id, "iat": now, "exp": now + timedelta(minutes=5),
             "iss": settings.auth_issuer, "aud": settings.auth_audience},
            settings.auth_secret, algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def test_normal_user_sees_only_their_intake_and_cannot_triage(self):
        listed = self.client.get("/api/fra/intake", headers=self.headers("intake-user"))
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([item["id"] for item in listed.json()["items"]], [self.intake_id])
        denied = self.client.patch(
            f"/api/fra/intake/{self.intake_id}", headers=self.headers("intake-user"),
            json={"target_state": "not_fra", "expected_revision": 0, "reasons": ["x"]},
        )
        self.assertEqual(denied.status_code, 403)

    def test_reviewer_triages_and_promotes_without_duplicate_native_claim(self):
        reviewer_headers = self.headers("intake-api-reviewer")
        reviewed = self.client.patch(
            f"/api/fra/intake/{self.intake_id}", headers=reviewer_headers,
            json={
                "target_state": "ready_for_promotion", "expected_revision": 0,
                "reasons": ["Verified intake"],
                "triage": {"right_type": "IFR", "rights_holder_id": self.holder_id},
            },
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        promoted = self.client.post(
            f"/api/fra/intake/{self.intake_id}/promote", headers=reviewer_headers,
            json={
                "right_type": "IFR", "rights_holder_id": self.holder_id,
                "gram_sabha_id": None, "expected_revision": 1,
            },
        )
        self.assertEqual(promoted.status_code, 201, promoted.text)
        repeated = self.client.post(
            f"/api/fra/intake/{self.intake_id}/promote", headers=reviewer_headers,
            json={
                "right_type": "IFR", "rights_holder_id": self.holder_id,
                "gram_sabha_id": None, "expected_revision": 2,
            },
        )
        self.assertEqual(repeated.status_code, 201, repeated.text)
        self.assertEqual(repeated.json()["claim_id"], promoted.json()["claim_id"])


if __name__ == "__main__":
    unittest.main()
