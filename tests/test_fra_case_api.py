import unittest
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import settings
from app.db.base import Base
from app.db.fra_models import FRAClaim, RightsHolder
from app.db.models import User
from app.db.session import get_db
from app.main import app
from app.services.audit import record_audit
from app.services.fra_claims import create_claim


class FRACaseAPITests(unittest.TestCase):
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
            owner = User(external_id="case-owner", display_name="Case owner", role="user")
            other = User(external_id="case-other", display_name="Other user", role="user")
            reviewer = User(external_id="case-reviewer", display_name="Reviewer", role="reviewer")
            session.add_all([owner, other, reviewer]); session.flush()
            first_holder = RightsHolder(
                display_name="Ramu", holder_type="individual", external_reference="case-holder-1",
                metadata_json={"private_phone": "not-for-listing"},
            )
            second_holder = RightsHolder(
                display_name="Mala", holder_type="individual", external_reference="case-holder-2",
            )
            session.add_all([first_holder, second_holder]); session.flush()
            first = create_claim(
                session, claim_number="TN-IFR-001", right_type="IFR",
                rights_holder_id=first_holder.id, submitted_by=owner.id,
                claimed_area_sqm=1200, provenance={"source": "test"},
            )
            first.status = "submitted"
            second = create_claim(
                session, claim_number="TN-IFR-002", right_type="IFR",
                rights_holder_id=second_holder.id, submitted_by=other.id,
                claimed_area_sqm=800, provenance={"source": "test"},
            )
            record_audit(
                session, actor_id=owner.id, action="fra_case_test_event",
                entity_type="fra_claim", entity_id=first.id,
                after={"status": "submitted", "private_uri": "private://hidden"},
            )
            session.commit()
            self.first_id, self.second_id = str(first.id), str(second.id)
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

    def test_user_lists_only_owned_cases_while_reviewer_can_filter_all(self):
        owned = self.client.get("/api/fra/cases", headers=self.headers("case-owner"))
        self.assertEqual(owned.status_code, 200)
        self.assertEqual([item["id"] for item in owned.json()["items"]], [self.first_id])
        reviewed = self.client.get(
            "/api/fra/cases?status=submitted&right_type=IFR&query=Ramu",
            headers=self.headers("case-reviewer"),
        )
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual([item["claim_number"] for item in reviewed.json()["items"]], ["TN-IFR-001"])
        self.assertNotIn("private_phone", reviewed.text)

    def test_case_detail_is_role_aware_and_redacts_private_audit_values(self):
        hidden = self.client.get(
            f"/api/fra/cases/{self.second_id}", headers=self.headers("case-owner")
        )
        self.assertEqual(hidden.status_code, 404)
        detail = self.client.get(
            f"/api/fra/cases/{self.first_id}", headers=self.headers("case-reviewer")
        )
        self.assertEqual(detail.status_code, 200)
        body = detail.json()
        self.assertEqual(body["rights_holder"]["display_name"], "Ramu")
        self.assertEqual(body["allowed_transitions"], ["gram_sabha_verified", "remanded", "withdrawn"])
        self.assertEqual(body["audit_timeline"][-1]["action"], "fra_case_test_event")
        self.assertNotIn("private://hidden", detail.text)
        self.assertNotIn("private_uri", detail.text)

    def test_case_reference_lists_support_case_and_intake_forms(self):
        holders = self.client.get(
            "/api/fra/case-reference/rights-holders", headers=self.headers("case-reviewer")
        )
        self.assertEqual(holders.status_code, 200)
        self.assertEqual({item["display_name"] for item in holders.json()["items"]}, {"Ramu", "Mala"})
        denied = self.client.get(
            "/api/fra/case-reference/rights-holders", headers=self.headers("case-owner")
        )
        self.assertEqual(denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
