import unittest
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import settings
from app.db.base import Base
from app.db.fra_completion_models import FRAVillageProfile
from app.db.fra_models import DSSRecommendation, FRAClaim, FRAGeometryVersion, RightsHolder, SchemeRuleSet
from app.db.fra_operational_models import ImageryArtifact
from app.db.models import User
from app.db.session import get_db
from app.main import app
from tests.test_fra_reports import BOUNDARY


class FRAPlanningAPITests(unittest.TestCase):
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
            staff = User(external_id="planning-staff", display_name="Staff", role="user")
            reviewer = User(external_id="planning-reviewer", display_name="Reviewer", role="reviewer")
            admin = User(external_id="planning-admin", display_name="Admin", role="admin")
            session.add_all([staff, reviewer, admin]); session.flush()
            holder = RightsHolder(display_name="Synthetic holder", holder_type="individual")
            claim = FRAClaim(
                claim_number="TN-PLAN-1", right_type="IFR", status="granted",
                rights_holder=holder, submitted_by=reviewer.id, provenance_json={"synthetic": True},
            )
            rule = SchemeRuleSet(
                scheme_code="DEMO-WATER", display_name="Demo Water", version="demo-v1",
                required_facts_json=[], condition_json={"present": {"fact": "x"}},
                recommendation_text="Refer for review", source_reference="demo://rule",
                created_by=reviewer.id,
            )
            village = FRAVillageProfile(
                state_code="TN", state_name="Tamil Nadu", district_code="TN-13",
                district_name="Thanjavur", block_code="TN-13-01", block_name="Kumbakonam",
                village_code="TN-13-01-001", village_name="Kottur Demo", boundary=BOUNDARY,
                tribal_groups_json=[], socioeconomic_json={}, provenance_json={"synthetic": True},
                reference_version="demo-v1", synthetic=True,
            )
            session.add_all([claim, rule, village]); session.flush()
            geometry = FRAGeometryVersion(claim=claim, version=1, geometry=BOUNDARY, source="survey", boundary_quality="surveyed", created_by=reviewer.id)
            session.add(geometry); session.flush()
            session.add(ImageryArtifact(claim_id=claim.id, geometry_version_id=geometry.id, artifact_type="historical_land_observation:2005", target_year=2005, storage_key="private/evidence.json", content_sha256="a" * 64, processor_version="history-v1", parameters_json={}, statistics_json={"forest_index": .5}, quality_flags_json=["supporting_observation"], provenance_json={"legal_role": "supporting_observation"}, state="completed", verification_state="unverified"))
            recommendation = DSSRecommendation(
                claim=claim, rule_set=rule, rule_version=rule.version, actor_id=reviewer.id,
                idempotency_key="plan-eval", outcome="recommended", input_json={"facts": {}},
                output_json={"reasons": ["Demo reason"], "missing_inputs": [], "advisory_only": True},
            )
            session.add(recommendation); session.commit()
            self.recommendation_id = str(recommendation.id)
            self.village_id = str(village.id)
            self.claim_id = str(claim.id)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); app.dependency_overrides.clear(); self.engine.dispose()

    @staticmethod
    def headers(external_id="planning-staff"):
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {"sub": external_id, "iat": now, "exp": now + timedelta(minutes=5),
             "iss": settings.auth_issuer, "aud": settings.auth_audience},
            settings.auth_secret, algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def test_recommendations_are_advisory_and_referrals_require_reviewer(self):
        listed = self.client.get("/api/fra/dss/recommendations", headers=self.headers())
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertTrue(listed.json()["items"][0]["advisory_only"])
        payload = {"department": "Rural Development", "priority": "high", "idempotency_key": "api-ref-1"}
        denied = self.client.post(
            f"/api/fra/dss/recommendations/{self.recommendation_id}/referrals",
            headers=self.headers(), json=payload,
        )
        self.assertEqual(denied.status_code, 403)
        created = self.client.post(
            f"/api/fra/dss/recommendations/{self.recommendation_id}/referrals",
            headers=self.headers("planning-reviewer"), json=payload,
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertTrue(created.json()["advisory_only"])
        updated = self.client.patch(
            f"/api/fra/dss/referrals/{created.json()['id']}",
            headers=self.headers("planning-reviewer"),
            json={"status": "under_review", "notes": "Assigned", "expected_revision": 0},
        )
        self.assertEqual(updated.status_code, 200, updated.text)

    def test_printable_village_report_is_private_and_no_store(self):
        response = self.client.get(
            f"/api/fra/reports/villages/{self.village_id}", headers=self.headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertIn("advisory and do not approve or sanction", response.text)

    def test_historical_report_is_claim_scoped_private_and_no_store(self):
        denied = self.client.get(
            f"/api/fra/reports/claims/{self.claim_id}/historical-evidence",
            headers=self.headers("planning-staff"),
        )
        self.assertEqual(denied.status_code, 404)
        response = self.client.get(
            f"/api/fra/reports/claims/{self.claim_id}/historical-evidence",
            headers=self.headers("planning-reviewer"),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertNotIn("private/evidence.json", response.text)
        self.assertIn("supporting evidence", response.text)

    def test_derive_and_evaluate_uses_a_versioned_fact_snapshot(self):
        response = self.client.post(
            "/api/fra/dss/derive-and-evaluate",
            headers={**self.headers("planning-reviewer"), "Idempotency-Key": "derive-api-1"},
            json={"claim_id": self.claim_id, "derivation_version": "tn-facts-v1"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["fact_snapshot"]["derivation_version"], "tn-facts-v1")
        self.assertIn("has_active_title", response.json()["fact_snapshot"]["facts"])
        self.assertTrue(response.json()["recommendations"][0]["advisory_only"])

    def test_scheme_catalog_requires_admin_and_retains_approval_provenance(self):
        payload = {"scheme_code": "JJM", "display_name": "Jal Jeevan Mission", "version": "tn-2026", "department": "Rural Development", "description": "Approved planning reference", "effective_from": "2026-08-01", "approving_authority": "Tamil Nadu competent authority", "source_reference": "https://example.gov.in/jjm", "definition": {"reviewed_on": "2026-08-01"}, "authoritative": True, "active": True}
        denied = self.client.post("/api/fra/dss/scheme-catalog", headers=self.headers("planning-reviewer"), json=payload)
        self.assertEqual(denied.status_code, 403)
        created = self.client.post("/api/fra/dss/scheme-catalog", headers=self.headers("planning-admin"), json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["approving_authority"], "Tamil Nadu competent authority")
        listed = self.client.get("/api/fra/dss/scheme-catalog", headers=self.headers())
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["scheme_code"], "JJM")


if __name__ == "__main__":
    unittest.main()
