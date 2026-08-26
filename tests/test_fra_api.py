import unittest
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import settings
from app.db.base import Base
from app.db.fra_models import DSSRecommendation, FRAEvidenceItem, SatelliteObservation
from app.db.models import AuditEvent, Claim, Document, Parcel, User
from app.db.session import get_db
from app.main import app


POLYGON = {
    "type": "Polygon",
    "coordinates": [[[79.0, 10.0], [79.002, 10.0], [79.002, 10.002], [79.0, 10.002], [79.0, 10.0]]],
}
MULTIPOLYGON = {"type": "MultiPolygon", "coordinates": [POLYGON["coordinates"]]}


class FRAAPITests(unittest.TestCase):
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
            session.add_all([
                User(external_id="staff", display_name="Staff", role="user"),
                User(external_id="reviewer", display_name="Reviewer", role="reviewer"),
                User(external_id="admin", display_name="Admin", role="admin"),
            ])
            session.commit()
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    @staticmethod
    def headers(external_id="staff", request_id="fra-test", idempotency_key=None):
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
        result = {"Authorization": f"Bearer {token}", "X-Request-ID": request_id}
        if idempotency_key:
            result["Idempotency-Key"] = idempotency_key
        return result

    def create_holder(self, **overrides):
        payload = {
            "display_name": "Ramu Naik",
            "holder_type": "individual",
            "claimant_category": "ST",
            "external_reference": "private-holder-reference",
        }
        payload.update(overrides)
        return self.client.post("/api/fra/rights-holders", headers=self.headers(), json=payload)

    def create_claim(self, claim_number="IFR-API-1"):
        holder = self.create_holder(external_reference=f"private-{claim_number}").json()
        response = self.client.post(
            "/api/fra/claims",
            headers=self.headers(),
            json={
                "claim_number": claim_number,
                "right_type": "IFR",
                "rights_holder_id": holder["id"],
            },
        )
        return response

    def add_geometry(self, claim_id):
        return self.client.post(
            f"/api/fra/claims/{claim_id}/geometries",
            headers=self.headers(),
            json={
                "geometry": POLYGON,
                "source": "claimant_sketch",
                "boundary_quality": "unverified",
                "provenance": {"source": "test"},
            },
        )

    def test_fra_mutation_rejects_anonymous_client(self):
        response = self.client.post(
            "/api/fra/rights-holders",
            json={"display_name": "Ramu", "holder_type": "individual"},
        )
        self.assertEqual(response.status_code, 401)

    def test_staff_can_create_holder_and_claim_without_leaking_external_reference(self):
        holder = self.create_holder()
        self.assertEqual(holder.status_code, 201)
        self.assertNotIn("external_reference", holder.json())
        claim = self.client.post(
            "/api/fra/claims",
            headers=self.headers(),
            json={
                "claim_number": "IFR-001",
                "right_type": "IFR",
                "rights_holder_id": holder.json()["id"],
            },
        )
        self.assertEqual(claim.status_code, 201)
        self.assertNotEqual(claim.json()["submitted_by"], claim.json()["rights_holder_id"])
        detailed = self.client.get(f"/api/fra/claims/{claim.json()['id']}", headers=self.headers())
        self.assertNotIn("external_reference", detailed.text)
        privileged = self.client.get(
            f"/api/fra/claims/{claim.json()['id']}", headers=self.headers("reviewer")
        )
        self.assertEqual(privileged.json()["rights_holder"]["external_reference"], "private-holder-reference")

    def test_geometry_is_normalized_and_invalid_geometry_is_rejected(self):
        claim_id = self.create_claim().json()["id"]
        created = self.add_geometry(claim_id)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["geometry"]["type"], "MultiPolygon")
        invalid = self.client.post(
            f"/api/fra/claims/{claim_id}/geometries",
            headers=self.headers(),
            json={"geometry": {"type": "Point", "coordinates": [79, 10]}, "source": "test"},
        )
        self.assertEqual(invalid.status_code, 422)

    def test_transition_and_title_require_reviewer_and_map_invalid_state(self):
        claim_id = self.create_claim().json()["id"]
        denied = self.client.post(
            f"/api/fra/claims/{claim_id}/transitions",
            headers=self.headers(),
            json={"target_status": "submitted", "authority_level": "frc", "outcome": "submitted", "reasons": []},
        )
        self.assertEqual(denied.status_code, 403)
        accepted = self.client.post(
            f"/api/fra/claims/{claim_id}/transitions",
            headers=self.headers("reviewer"),
            json={"target_status": "submitted", "authority_level": "frc", "outcome": "submitted", "reasons": []},
        )
        self.assertEqual(accepted.status_code, 200)
        invalid = self.client.post(
            f"/api/fra/claims/{claim_id}/transitions",
            headers=self.headers("reviewer"),
            json={"target_status": "granted", "authority_level": "dlc", "outcome": "granted", "reasons": []},
        )
        self.assertEqual(invalid.status_code, 409)
        denied_title = self.client.post(
            f"/api/fra/claims/{claim_id}/titles",
            headers=self.headers(),
            json={"title_number": "T-1"},
        )
        self.assertEqual(denied_title.status_code, 403)

    def test_reviewer_can_complete_lifecycle_and_issue_a_versioned_title(self):
        claim_id = self.create_claim("IFR-TITLE-1").json()["id"]
        geometry_id = self.add_geometry(claim_id).json()["id"]
        transitions = [
            ("submitted", "frc"),
            ("gram_sabha_verified", "gram_sabha"),
            ("sdlc_review", "sdlc"),
            ("dlc_decided", "dlc"),
            ("granted", "dlc"),
        ]
        for target, authority in transitions:
            response = self.client.post(
                f"/api/fra/claims/{claim_id}/transitions",
                headers=self.headers("reviewer"),
                json={
                    "target_status": target, "authority_level": authority,
                    "outcome": target, "reasons": [],
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
        title = self.client.post(
            f"/api/fra/claims/{claim_id}/titles",
            headers=self.headers("reviewer"),
            json={"title_number": "TITLE-API-1", "geometry_version_id": geometry_id},
        )
        self.assertEqual(title.status_code, 201)
        self.assertEqual(title.json()["version"], 1)
        self.assertTrue(title.json()["active"])

    def test_evidence_creation_is_audited(self):
        claim_id = self.create_claim().json()["id"]
        response = self.client.post(
            f"/api/fra/claims/{claim_id}/evidence",
            headers=self.headers(),
            json={
                "category": "oral_statement",
                "source": "Gram Sabha meeting",
                "description": "Statement recorded in the meeting minutes.",
            },
        )
        self.assertEqual(response.status_code, 201)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(FRAEvidenceItem)), 1)
            audit = session.scalar(select(AuditEvent).where(AuditEvent.action == "fra_evidence_created"))
            self.assertIsNotNone(audit)

    def test_legacy_promotion_is_idempotent(self):
        holder_id = self.create_holder().json()["id"]
        with self.factory() as session:
            staff = session.scalar(select(User).where(User.external_id == "staff"))
            parcel = Parcel(
                state="Odisha", district="Mayurbhanj", taluk="Test", village="Test",
                survey_number="1", subdivision_number="", geometry=MULTIPOLYGON,
                source="Synthetic test data",
            )
            document = Document(
                uploader=staff, storage_key="private/test", original_filename="test.png",
                content_type="image/png", sha256="a" * 64, ocr_status="completed",
                idempotency_key="legacy-doc",
            )
            legacy = Claim(
                claimant=staff, parcel=parcel, document=document, confirmed_fields_json={},
                status="matched", match_method="exact", idempotency_key="legacy-claim",
            )
            session.add(legacy); session.commit(); legacy_id = str(legacy.id)
        payload = {"rights_holder_id": holder_id, "right_type": "IFR"}
        first = self.client.post(
            f"/api/fra/claims/promote-legacy/{legacy_id}", headers=self.headers(), json=payload
        )
        second = self.client.post(
            f"/api/fra/claims/promote-legacy/{legacy_id}", headers=self.headers(), json=payload
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["id"], second.json()["id"])

    def test_spatial_block_returns_conflict_and_hides_related_claim_from_normal_user(self):
        first_id = self.create_claim("IFR-SPATIAL-1").json()["id"]
        self.add_geometry(first_id)
        self.client.post(
            f"/api/fra/claims/{first_id}/transitions",
            headers=self.headers("reviewer"),
            json={"target_status": "submitted", "authority_level": "frc", "outcome": "submitted", "reasons": []},
        )
        second_id = self.create_claim("IFR-SPATIAL-2").json()["id"]
        normal = self.client.post(
            f"/api/fra/claims/{second_id}/spatial-evaluation",
            headers=self.headers(), json={"geometry": POLYGON},
        )
        self.assertEqual(normal.status_code, 409)
        self.assertNotIn(first_id, normal.text)
        reviewer = self.client.post(
            f"/api/fra/claims/{second_id}/spatial-evaluation",
            headers=self.headers("reviewer"), json={"geometry": POLYGON},
        )
        self.assertEqual(reviewer.status_code, 409)
        self.assertIn(first_id, reviewer.text)

    def test_community_claim_uses_gram_sabha_and_shared_overlap_requires_review(self):
        existing_id = self.create_claim("IFR-LAYER-1").json()["id"]
        self.add_geometry(existing_id)
        self.client.post(
            f"/api/fra/claims/{existing_id}/transitions",
            headers=self.headers("reviewer"),
            json={"target_status": "submitted", "authority_level": "frc", "outcome": "submitted"},
        )
        gram_sabha = self.client.post(
            "/api/fra/gram-sabhas", headers=self.headers(),
            json={"name": "Test Gram Sabha", "village": "Test Village"},
        )
        self.assertEqual(gram_sabha.status_code, 201)
        holder = self.create_holder(
            display_name="Test Community", holder_type="community",
            external_reference="private-community", gram_sabha_id=gram_sabha.json()["id"],
        )
        claim = self.client.post(
            "/api/fra/claims", headers=self.headers(),
            json={
                "claim_number": "CFR-LAYER-1", "right_type": "CFR",
                "rights_holder_id": holder.json()["id"],
                "gram_sabha_id": gram_sabha.json()["id"],
            },
        )
        self.assertEqual(claim.status_code, 201)
        result = self.client.post(
            f"/api/fra/claims/{claim.json()['id']}/spatial-evaluation",
            headers=self.headers("reviewer"), json={"geometry": POLYGON},
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["outcome"], "review_required")
        self.assertEqual(result.json()["findings"][0]["related_claim_id"], existing_id)

    def test_missing_fra_resources_return_not_found(self):
        response = self.client.get(
            "/api/fra/claims/00000000-0000-0000-0000-000000000001",
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 404)

    def test_satellite_route_creates_only_supporting_evidence_and_maps_unavailable_provider(self):
        claim_id = self.create_claim().json()["id"]
        self.add_geometry(claim_id)
        unavailable = self.client.post(
            f"/api/fra/claims/{claim_id}/satellite-observations",
            headers=self.headers(), json={"scene_id": "missing"},
        )
        self.assertEqual(unavailable.status_code, 503)
        response = self.client.post(
            f"/api/fra/claims/{claim_id}/satellite-observations",
            headers=self.headers(),
            json={
                "scene_id": "scene-2005", "provider": "local-manifest",
                "source_uri": "private://scene-2005", "acquired_at": "2005-01-15",
                "analyser_version": "local-v1",
                "observations": [{"asset_class": "forest_cover", "value": 0.72, "confidence": 0.83}],
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()[0]["legal_role"], "supporting")
        self.assertNotRegex(response.text.casefold(), r'"(valid|invalid|approved|rejected)"')
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(SatelliteObservation)), 1)

    def test_admin_rule_creation_and_advisory_dss_are_validated_and_idempotent(self):
        claim_id = self.create_claim().json()["id"]
        rule = {
            "scheme_code": "DEMO-WATER", "display_name": "Demo Water Support",
            "version": "demo-v1", "required_facts": ["has_water"],
            "condition": {"eq": {"fact": "has_water", "value": False}},
            "recommendation_text": "Refer for departmental water-support review.",
            "source_reference": "demo://water-support",
        }
        self.assertEqual(
            self.client.post("/api/fra/dss/rule-sets", headers=self.headers(), json=rule).status_code,
            403,
        )
        invalid = dict(rule, condition={"execute": "anything"})
        self.assertEqual(
            self.client.post("/api/fra/dss/rule-sets", headers=self.headers("admin"), json=invalid).status_code,
            422,
        )
        created = self.client.post(
            "/api/fra/dss/rule-sets", headers=self.headers("admin"), json=rule
        )
        self.assertEqual(created.status_code, 201)
        evaluation = {"claim_id": claim_id, "facts": {"has_water": False}}
        first = self.client.post(
            "/api/fra/dss/evaluate",
            headers=self.headers(idempotency_key="dss-1"), json=evaluation,
        )
        second = self.client.post(
            "/api/fra/dss/evaluate",
            headers=self.headers(idempotency_key="dss-1"), json=evaluation,
        )
        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.json()[0]["advisory_only"])
        self.assertEqual(first.json()[0]["id"], second.json()[0]["id"])
        fetched = self.client.get(
            f"/api/fra/dss/recommendations/{first.json()[0]['id']}", headers=self.headers()
        )
        self.assertTrue(fetched.json()["advisory_only"])
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(DSSRecommendation)), 1)


if __name__ == "__main__":
    unittest.main()
