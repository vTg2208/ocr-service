import unittest
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import settings
from app.db.base import Base
from app.db.fra_models import RightsHolder, FRAClaim
from app.db.fra_operational_models import FRAIntakeItem
from app.db.models import Claim, Document, Parcel, User, AuditEvent
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
            self.legacy_id = str(legacy.id)
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


    def test_both_promotion_routes_require_review_revision_and_matching_replays(self):
        routes = [f"/api/fra/claims/promote-legacy/{self.legacy_id}", f"/api/fra/intake/{self.intake_id}/promote"]
        payload = {"right_type": "IFR", "rights_holder_id": self.holder_id, "expected_revision": 0}
        reviewer = self.headers("intake-api-reviewer")
        for route in routes:
            with self.subTest(route=route):
                self.assertEqual(self.client.post(route, headers=self.headers("intake-user"), json=payload).status_code, 403)
                self.assertEqual(self.client.post(route, headers=reviewer, json=payload).status_code, 409)
        reviewed = self.client.patch(f"/api/fra/intake/{self.intake_id}", headers=reviewer,
            json={"target_state": "ready_for_promotion", "expected_revision": 0, "reasons": ["Verified"]})
        self.assertEqual(reviewed.status_code, 200)
        for route in routes:
            self.assertEqual(self.client.post(route, headers=reviewer, json=payload).status_code, 409)
        first = self.client.post(routes[0], headers=reviewer, json={**payload, "expected_revision": 1})
        self.assertEqual(first.status_code, 201, first.text)
        claim_id = first.json()["id"]
        self.assertIn("geometry_versions", first.json())
        self.assertEqual(first.json()["intake_state"], "promoted")
        self.assertEqual(first.json()["revision"], 2)
        with self.factory() as session:
            audit_count = session.scalar(select(func.count()).select_from(AuditEvent))
        for route in routes:
            repeated = self.client.post(route, headers=reviewer, json={**payload, "expected_revision": 2})
            self.assertEqual(repeated.status_code, 201, repeated.text)
            self.assertEqual(repeated.json().get("claim_id", repeated.json().get("id")), claim_id)
            for change in ({"right_type": "CFR"}, {"rights_holder_id": str(uuid.uuid4())}, {"gram_sabha_id": str(uuid.uuid4())}):
                with self.subTest(route=route, change=change):
                    mismatch = self.client.post(route, headers=reviewer, json={**payload, "expected_revision": 2, **change})
                    self.assertEqual(mismatch.status_code, 409, mismatch.text)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(FRAClaim)), 1)
            intake = session.get(FRAIntakeItem, uuid.UUID(self.intake_id))
            self.assertEqual(intake.state, "promoted")
            self.assertEqual(str(intake.promoted_claim_id), claim_id)
            self.assertEqual(intake.revision, 2)
            self.assertEqual(session.scalar(select(func.count()).select_from(AuditEvent)), audit_count)

    def test_compatibility_promotion_requires_intake_and_explicit_revision(self):
        route = f"/api/fra/claims/promote-legacy/{self.legacy_id}"
        payload = {"right_type": "IFR", "rights_holder_id": self.holder_id}
        headers = self.headers("intake-api-reviewer")
        self.assertEqual(self.client.post(route, headers=headers, json=payload).status_code, 422)
        with self.factory() as session:
            session.delete(session.get(FRAIntakeItem, uuid.UUID(self.intake_id)))
            session.commit()
        response = self.client.post(route, headers=headers, json={**payload, "expected_revision": 0})
        self.assertEqual(response.status_code, 404, response.text)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(FRAClaim)), 0)


    def test_intake_replay_rejects_changed_parameters(self):
        headers = self.headers("intake-api-reviewer")
        self.client.patch(f"/api/fra/intake/{self.intake_id}", headers=headers,
            json={"target_state": "ready_for_promotion", "expected_revision": 0, "reasons": ["Verified"]})
        route = f"/api/fra/intake/{self.intake_id}/promote"
        payload = {"right_type": "IFR", "rights_holder_id": self.holder_id, "expected_revision": 1}
        self.assertEqual(self.client.post(route, headers=headers, json=payload).status_code, 201)
        changed = self.client.post(route, headers=headers, json={**payload, "expected_revision": 2, "right_type": "CFR"})
        self.assertEqual(changed.status_code, 409, changed.text)

    def test_compatibility_promotion_does_not_bypass_missing_intake(self):
        with self.factory() as session:
            session.delete(session.get(FRAIntakeItem, uuid.UUID(self.intake_id)))
            session.commit()
        response = self.client.post(f"/api/fra/claims/promote-legacy/{self.legacy_id}",
            headers=self.headers("intake-api-reviewer"),
            json={"right_type": "IFR", "rights_holder_id": self.holder_id, "expected_revision": 0})
        self.assertEqual(response.status_code, 404, response.text)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(FRAClaim)), 0)


    def test_preexisting_legacy_promotion_requires_matching_reviewed_parameters(self):
        with self.factory() as session:
            reviewer = session.scalar(select(User).where(User.external_id == "intake-api-reviewer"))
            legacy = session.get(Claim, uuid.UUID(self.legacy_id))
            existing = FRAClaim(
                claim_number=f"LEGACY-{legacy.id}", right_type="IFR", status="draft",
                rights_holder_id=uuid.UUID(self.holder_id), submitted_by=reviewer.id,
                legacy_claim_id=legacy.id, parcel_id=legacy.parcel_id,
                document_id=legacy.document_id, provenance_json={"source": "pre-intake-migration"},
            )
            session.add(existing)
            session.commit()
            existing_id = str(existing.id)
        headers = self.headers("intake-api-reviewer")
        reviewed = self.client.patch(f"/api/fra/intake/{self.intake_id}", headers=headers,
            json={"target_state": "ready_for_promotion", "expected_revision": 0, "reasons": ["Verified"]})
        self.assertEqual(reviewed.status_code, 200)
        with self.factory() as session:
            audit_count = session.scalar(select(func.count()).select_from(AuditEvent))
        route = f"/api/fra/claims/promote-legacy/{self.legacy_id}"
        payload = {"right_type": "CFR", "rights_holder_id": self.holder_id, "expected_revision": 1}
        denied = self.client.post(route, headers=headers, json=payload)
        self.assertEqual(denied.status_code, 409, denied.text)
        with self.factory() as session:
            intake = session.get(FRAIntakeItem, uuid.UUID(self.intake_id))
            self.assertEqual(intake.state, "ready_for_promotion")
            self.assertIsNone(intake.promoted_claim_id)
            self.assertEqual(session.scalar(select(func.count()).select_from(AuditEvent)), audit_count)
        accepted = self.client.post(route, headers=headers, json={**payload, "right_type": "IFR"})
        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.assertEqual(accepted.json()["id"], existing_id)


if __name__ == "__main__":
    unittest.main()
