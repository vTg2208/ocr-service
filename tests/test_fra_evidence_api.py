import unittest
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import settings
from app.db.base import Base
from app.db.fra_completion_models import ModelVersion
from app.db.fra_models import FRAClaim, FRAGeometryVersion, RightsHolder
from app.db.fra_operational_models import ImageryArtifact, ImagerySceneRecord
from app.db.models import User
from app.db.session import get_db
from app.main import app


GEOMETRY = {"type": "MultiPolygon", "coordinates": [[[[79, 10], [79.1, 10], [79.1, 10.1], [79, 10], [79, 10]]]]}


class FRAEvidenceAPITests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine); self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        def override():
            with self.factory() as session: yield session
        app.dependency_overrides[get_db] = override
        with self.factory() as session:
            owner = User(external_id="evidence-owner", display_name="Owner", role="user")
            other = User(external_id="evidence-other", display_name="Other", role="user")
            reviewer = User(external_id="evidence-reviewer", display_name="Reviewer", role="reviewer")
            holder = RightsHolder(display_name="Ramu", holder_type="individual")
            session.add_all([owner, other, reviewer, holder]); session.flush()
            claim = FRAClaim(claim_number="TN-EVID-1", right_type="IFR", status="submitted", rights_holder_id=holder.id, submitted_by=owner.id)
            no_geometry = FRAClaim(claim_number="TN-EVID-2", right_type="IFR", status="draft", rights_holder_id=holder.id, submitted_by=owner.id)
            other_claim = FRAClaim(claim_number="TN-EVID-3", right_type="IFR", status="draft", rights_holder_id=holder.id, submitted_by=other.id)
            session.add_all([claim, no_geometry, other_claim]); session.flush()
            geometry = FRAGeometryVersion(claim=claim, version=1, geometry=GEOMETRY, source="survey", boundary_quality="surveyed", created_by=owner.id)
            session.add(geometry); session.flush()
            scene = ImagerySceneRecord(provider="stac.test", collection="landsat-c2-l2", scene_id="scene-2005", acquired_at=datetime(2005, 6, 1, tzinfo=timezone.utc), footprint=GEOMETRY, cloud_cover=8, asset_references_json={"visual": {"href": "https://data.test/a?secret=x"}}, license_reference="https://example.org/license", provenance_json={})
            model = ModelVersion(task="historical_evidence", adapter_type="rest", name="history", version="v1", status="active", configuration_json={"ready": True}, label_map_json={}, metrics_json={}, registered_by=reviewer.id)
            session.add_all([scene, model]); session.flush()
            session.add(ImageryArtifact(claim_id=claim.id, geometry_version_id=geometry.id, imagery_scene_id=scene.id, artifact_type="historical_land_observation:2005", target_year=2005, storage_key="private/artifact.json", content_sha256="a"*64, processor_version="v1", model_version_id=model.id, parameters_json={}, statistics_json={"forest_index": .62}, quality_flags_json=["supporting_observation"], provenance_json={"legal_role": "supporting_observation"}, state="completed", verification_state="unverified"))
            session.commit(); self.claim_id, self.no_geometry_id, self.other_claim_id = str(claim.id), str(no_geometry.id), str(other_claim.id)
        self.client = TestClient(app)

    def tearDown(self): self.client.close(); app.dependency_overrides.clear(); self.engine.dispose()

    @staticmethod
    def headers(user="evidence-owner", key="historical-api-1"):
        now = datetime.now(timezone.utc); token = jwt.encode({"sub": user, "iat": now, "exp": now + timedelta(minutes=5), "iss": settings.auth_issuer, "aud": settings.auth_audience}, settings.auth_secret, algorithm="HS256")
        return {"Authorization": f"Bearer {token}", "Idempotency-Key": key}

    def test_request_is_idempotent_and_owner_can_list_redacted_status(self):
        first = self.client.post(f"/api/fra/claims/{self.claim_id}/historical-evidence", headers=self.headers(), json={"target_years": [2005, 2010]})
        second = self.client.post(f"/api/fra/claims/{self.claim_id}/historical-evidence", headers=self.headers(), json={"target_years": [2005, 2010]})
        self.assertEqual(first.status_code, 202, first.text); self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        listed = self.client.get(f"/api/fra/claims/{self.claim_id}/historical-evidence", headers=self.headers())
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["artifacts"][0]["target_year"], 2005)
        self.assertEqual(listed.json()["artifacts"][0]["provider"], "stac.test")
        self.assertNotIn("private/artifact", listed.text); self.assertNotIn("secret=x", listed.text)

    def test_permissions_and_geometry_precondition(self):
        hidden = self.client.post(f"/api/fra/claims/{self.other_claim_id}/historical-evidence", headers=self.headers(), json={"target_years": [2005]})
        self.assertEqual(hidden.status_code, 404)
        missing = self.client.post(f"/api/fra/claims/{self.no_geometry_id}/historical-evidence", headers=self.headers(key="no-geometry"), json={"target_years": [2005]})
        self.assertEqual(missing.status_code, 422)
        reviewer = self.client.get(f"/api/fra/claims/{self.other_claim_id}/historical-evidence", headers=self.headers("evidence-reviewer"))
        self.assertEqual(reviewer.status_code, 200)


if __name__ == "__main__": unittest.main()
