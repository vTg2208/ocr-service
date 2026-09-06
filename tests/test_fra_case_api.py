import unittest
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import settings
from app.db.base import Base
from app.db.fra_models import FRAClaim, RightsHolder, FRAEvidenceItem, FRAGeometryVersion, SatelliteObservation, DSSRecommendation, SchemeRuleSet
from app.db.fra_completion_models import AssetFeature, ModelVersion, ProcessingJob
from app.services.processing_jobs import enqueue_job
from app.db.models import User, Document, AuditEvent
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


    def counts(self):
        with self.factory() as session:
            return {model.__name__: session.scalar(select(func.count()).select_from(model))
                    for model in (FRAClaim, FRAEvidenceItem, FRAGeometryVersion,
                                  SatelliteObservation, DSSRecommendation, ProcessingJob, AuditEvent)}

    def test_case_bound_routes_hide_other_owners_before_work_or_audit(self):
        geometry = {"type": "Polygon", "coordinates": [[[78, 11], [78.01, 11], [78.01, 11.01], [78, 11.01], [78, 11]]]}
        paths = [
            ("GET", f"/api/fra/claims/{self.second_id}", None),
            ("POST", f"/api/fra/claims/{self.second_id}/geometries", {"geometry": geometry, "source": "test"}),
            ("POST", f"/api/fra/claims/{self.second_id}/evidence", {"category": "documentary", "description": "Recorded evidence", "source": "test"}),
            ("POST", f"/api/fra/claims/{self.second_id}/satellite-observations", {"scene_id": "missing"}),
            ("POST", f"/api/fra/claims/{self.second_id}/spatial-evaluation", {"geometry": geometry}),
            ("GET", f"/api/fra/claims/{self.second_id}/historical-evidence", None),
            ("POST", f"/api/fra/claims/{self.second_id}/historical-evidence", {"target_years": [2005]}),
            ("GET", f"/api/fra/reports/claims/{self.second_id}", None),
            ("GET", f"/api/fra/reports/claims/{self.second_id}/historical-evidence", None),
            ("POST", "/api/fra/dss/derive-and-evaluate", {"claim_id": self.second_id, "derivation_version": "tn-facts-v1"}),
            ("GET", f"/api/fra/assets?claim_id={self.second_id}", None),
            ("GET", f"/api/fra/dss/recommendations?claim_id={self.second_id}", None),
        ]
        for method, path, payload in paths:
            before = self.counts()
            with self.subTest(path=path):
                response = self.client.request(method, path,
                    headers={**self.headers("case-owner"), "Idempotency-Key": "hidden-case"}, json=payload)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(self.counts(), before)

    def test_document_attachment_requires_document_owner_or_reviewer(self):
        with self.factory() as session:
            other = session.scalar(select(User).where(User.external_id == "case-other"))
            document = Document(uploaded_by=other.id, storage_key="private/other.pdf",
                original_filename="other.pdf", content_type="application/pdf", sha256="d" * 64,
                idempotency_key="other-document")
            session.add(document); session.commit()
            document_id = str(document.id)
            holder_id = str(session.get(FRAClaim, uuid.UUID(self.first_id)).rights_holder_id)
        payloads = [
            ("/api/fra/claims", {"claim_number": "ATTACHED", "right_type": "IFR", "rights_holder_id": holder_id, "document_id": document_id}),
            (f"/api/fra/claims/{self.first_id}/evidence", {"category": "documentary", "description": "Recorded evidence", "source": "test", "document_id": document_id}),
        ]
        for path, payload in payloads:
            with self.subTest(path=path):
                before = self.counts()
                denied = self.client.post(path, headers=self.headers("case-owner"), json=payload)
                self.assertEqual(denied.status_code, 404, denied.text)
                self.assertEqual(self.counts(), before)
                accepted = self.client.post(path, headers=self.headers("case-reviewer"), json=payload)
                self.assertEqual(accepted.status_code, 201, accepted.text)
        owned = self.client.post(f"/api/fra/claims/{self.second_id}/evidence",
            headers=self.headers("case-other"), json=payloads[1][1])
        self.assertEqual(owned.status_code, 201, owned.text)

    def test_case_related_lists_details_and_inference_are_private(self):
        with self.factory() as session:
            owner = session.scalar(select(User).where(User.external_id == "case-owner"))
            reviewer = session.scalar(select(User).where(User.external_id == "case-reviewer"))
            rule = SchemeRuleSet(scheme_code="DEMO", display_name="Demo", version="v1",
                required_facts_json=[], condition_json={"present": {"fact": "x"}},
                recommendation_text="Review", source_reference="demo://rule", created_by=reviewer.id)
            model = ModelVersion(task="asset_detection", name="test", version="v1", adapter_type="manifest",
                status="active", label_map_json={}, metrics_json={}, configuration_json={"ready": True}, registered_by=reviewer.id)
            session.add_all([rule, model]); session.flush()
            recommendation_ids = []
            asset_ids = []
            job_ids = []
            for case_id in (self.first_id, self.second_id):
                case_uuid = uuid.UUID(case_id)
                recommendation = DSSRecommendation(claim_id=case_uuid, rule_set=rule,
                    rule_version="v1", actor_id=reviewer.id, idempotency_key=case_id,
                    outcome="recommended", input_json={}, output_json={"advisory_only": True})
                asset = AssetFeature(claim_id=case_uuid, asset_class="water_body",
                    point_geometry_json={"type": "Point", "coordinates": [78, 11]},
                    observed_value_json={"present": True}, source_type="model", provenance_json={})
                session.add_all([recommendation, asset])
                # Simulate historical jobs requested before the access bug was fixed.
                job = enqueue_job(session, task_type="asset_inference", entity_type="fra_claim",
                    entity_id=case_uuid, actor_id=owner.id, idempotency_key=case_id, payload={})
                session.flush()
                recommendation_ids.append(str(recommendation.id)); asset_ids.append(str(asset.id)); job_ids.append(str(job.id))
            shared = AssetFeature(asset_class="water_body", source_type="model", observed_value_json={}, provenance_json={})
            session.add(shared); session.commit()
            shared_id, model_id = str(shared.id), str(model.id)
        before = self.counts()
        for path in (f"/api/fra/dss/recommendations/{recommendation_ids[1]}", f"/api/fra/jobs/{job_ids[1]}"):
            with self.subTest(path=path):
                denied = self.client.get(path, headers=self.headers("case-owner"))
                self.assertEqual(denied.status_code, 404, denied.text)
                self.assertEqual(self.client.get(path, headers=self.headers("case-reviewer")).status_code, 200)
        for path, expected in (("/api/fra/assets", {asset_ids[0], shared_id}),
                               ("/api/fra/dss/recommendations", {recommendation_ids[0]}),
                               ("/api/fra/jobs?limit=1", {job_ids[0]})):
            with self.subTest(path=path):
                listed = self.client.get(path, headers=self.headers("case-owner"))
                self.assertEqual(listed.status_code, 200, listed.text)
                self.assertEqual({item["id"] for item in listed.json()["items"]}, expected)
        denied = self.client.post("/api/fra/assets/inference-jobs", headers=self.headers("case-owner"),
            json={"claim_id": self.second_id, "model_version_id": model_id, "scene_id": "test", "idempotency_key": "hidden", "manifest": {}})
        self.assertEqual(denied.status_code, 404, denied.text)
        self.assertEqual(self.counts(), before)
        for identity, case_id in (("case-owner", self.first_id), ("case-reviewer", self.second_id)):
            with self.subTest(identity=identity):
                geometry = {"type": "Polygon", "coordinates": [[[78, 11], [78.01, 11], [78.01, 11.01], [78, 11.01], [78, 11]]]}
                headers = self.headers(identity)
                created = self.client.post(f"/api/fra/claims/{case_id}/geometries", headers=headers,
                    json={"geometry": geometry, "source": "test"})
                self.assertEqual(created.status_code, 201, created.text)
                accepted = self.client.post("/api/fra/assets/inference-jobs", headers=headers,
                    json={"claim_id": case_id, "model_version_id": model_id,
                          "scene_id": "test", "idempotency_key": identity, "manifest": {}})
                self.assertEqual(accepted.status_code, 202, accepted.text)
                self.assertEqual(self.client.get(f"/api/fra/jobs/{accepted.json()['id']}", headers=headers).status_code, 200)

    def test_foundation_owner_and_reviewer_controls(self):
        for identity in ("case-owner", "case-reviewer"):
            with self.subTest(identity=identity):
                headers = self.headers(identity)
                self.assertEqual(self.client.get(f"/api/fra/claims/{self.first_id}", headers=headers).status_code, 200)
                response = self.client.post(f"/api/fra/claims/{self.first_id}/evidence", headers=headers,
                    json={"category": "documentary", "description": "Recorded evidence", "source": "test"})
                self.assertEqual(response.status_code, 201, response.text)
                self.assertEqual(self.client.get(f"/api/fra/reports/claims/{self.first_id}", headers=headers).status_code, 200)

    def test_only_reviewer_can_version_geometry_after_adjudication(self):
        with self.factory() as session:
            session.get(FRAClaim, uuid.UUID(self.first_id)).status = "granted"
            session.commit()
        geometry = {"type": "Polygon", "coordinates": [[[78, 11], [78.01, 11], [78.01, 11.01], [78, 11.01], [78, 11]]]}
        path = f"/api/fra/claims/{self.first_id}/geometries"
        denied = self.client.post(
            path, headers=self.headers("case-owner"),
            json={"geometry": geometry, "source": "owner edit after grant"},
        )
        self.assertEqual(denied.status_code, 422, denied.text)
        accepted = self.client.post(
            path, headers=self.headers("case-reviewer"),
            json={"geometry": geometry, "source": "reviewed correction"},
        )
        self.assertEqual(accepted.status_code, 201, accepted.text)


    def test_spatial_disposition_requires_reviewer_even_for_case_owner(self):
        path = f"/api/fra/claims/{self.first_id}/evidence"
        payload = {"category": "documentary", "description": "Recorded evidence", "source": "spatial_evaluation_disposition"}
        before = self.counts()
        denied = self.client.post(path, headers=self.headers("case-owner"), json=payload)
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(self.counts(), before)
        missing = self.client.post(path, headers=self.headers("case-reviewer"), json=payload)
        self.assertEqual(missing.status_code, 422, missing.text)
        geometry = {"type": "Polygon", "coordinates": [[[78, 11], [78.01, 11], [78.01, 11.01], [78, 11.01], [78, 11]]]}
        saved = self.client.post(f"/api/fra/claims/{self.first_id}/geometries", headers=self.headers("case-reviewer"),
            json={"geometry": geometry, "source": "survey"})
        self.assertEqual(saved.status_code, 201, saved.text)
        payload["provenance"] = {"geometry_version_id": saved.json()["id"]}
        accepted = self.client.post(path, headers=self.headers("case-reviewer"), json=payload)
        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.assertTrue(accepted.json()["source_verified"])
        self.client.post(f"/api/fra/claims/{self.first_id}/geometries", headers=self.headers("case-reviewer"),
            json={"geometry": geometry, "source": "new survey"})
        stale = self.client.post(path, headers=self.headers("case-reviewer"), json=payload)
        self.assertEqual(stale.status_code, 409, stale.text)


if __name__ == "__main__":
    unittest.main()
