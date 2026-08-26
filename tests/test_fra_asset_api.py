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
from app.db.fra_completion_models import AssetFeature, FRAVillageProfile, ModelVersion
from app.db.models import User
from app.db.session import get_db
from app.main import app
from tests.test_fra_assets import BOUNDARY, asset_manifest


class FRAAssetAPITests(unittest.TestCase):
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
            staff = User(external_id="asset-api-staff", display_name="Staff", role="user")
            reviewer = User(
                external_id="asset-api-reviewer", display_name="Reviewer", role="reviewer"
            )
            session.add_all([staff, reviewer])
            session.flush()
            village = FRAVillageProfile(
                state_code="TN", state_name="Tamil Nadu",
                district_code="TN-13", district_name="Thanjavur",
                block_code="TN-13-01", block_name="Kumbakonam",
                village_code="TN-13-01-001", village_name="Kottur Demo",
                boundary=BOUNDARY, tribal_groups_json=[], socioeconomic_json={},
                provenance_json={"synthetic": True}, reference_version="demo-v1", synthetic=True,
            )
            model = ModelVersion(
                task="asset_detection", name="tn-assets", version="0.1.0",
                adapter_type="manifest", status="active", label_map_json={},
                metrics_json={"status": "not_evaluated"}, configuration_json={"ready": True},
                registered_by=reviewer.id,
            )
            session.add_all([village, model])
            session.commit()
            self.village_id, self.model_id = str(village.id), str(model.id)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    @staticmethod
    def headers(external_id="asset-api-staff"):
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {"sub": external_id, "iat": now, "exp": now + timedelta(minutes=5),
             "iss": settings.auth_issuer, "aud": settings.auth_audience},
            settings.auth_secret, algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def test_inference_job_requires_auth_and_returns_supporting_warning(self):
        payload = {
            "village_id": self.village_id,
            "model_version_id": self.model_id,
            "scene_id": "tn-scene-2005",
            "idempotency_key": "asset-api-1",
            "manifest": asset_manifest(),
        }
        self.assertEqual(
            self.client.post("/api/fra/assets/inference-jobs", json=payload).status_code, 401
        )
        response = self.client.post(
            "/api/fra/assets/inference-jobs", headers=self.headers(), json=payload
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["state"], "queued")
        self.assertIn("supporting evidence", response.json()["warning"])

    def test_asset_list_hides_private_source_and_review_requires_reviewer(self):
        with self.factory() as session:
            village = session.get(FRAVillageProfile, uuid.UUID(self.village_id))
            asset = AssetFeature(
                village=village,
                asset_class="water_body",
                point_geometry_json={"type": "Point", "coordinates": [79.11, 10.71]},
                observed_value_json={"present": True},
                confidence=0.7,
                source_type="model",
                source_reference="private://scene-pixels",
                provenance_json={"synthetic": True},
                verification_state="unverified",
                synthetic=True,
            )
            session.add(asset)
            session.commit()
            asset_id = str(asset.id)
        listed = self.client.get(
            "/api/fra/assets?district=Thanjavur", headers=self.headers()
        )
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn("private://scene-pixels", listed.text)
        self.assertIn("supporting evidence", listed.json()["warning"])
        denied = self.client.post(
            f"/api/fra/assets/{asset_id}/review",
            headers=self.headers(),
            json={"outcome": "verified", "expected_revision": 0, "reasons": []},
        )
        self.assertEqual(denied.status_code, 403)
        reviewed = self.client.post(
            f"/api/fra/assets/{asset_id}/review",
            headers=self.headers("asset-api-reviewer"),
            json={"outcome": "verified", "expected_revision": 0, "reasons": []},
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(reviewed.json()["verification_state"], "verified")

    def test_inactive_model_is_explicitly_unavailable(self):
        with self.factory() as session:
            model = session.get(ModelVersion, uuid.UUID(self.model_id))
            model.status = "inactive"
            session.commit()
        response = self.client.post(
            "/api/fra/assets/inference-jobs",
            headers=self.headers(),
            json={
                "village_id": self.village_id,
                "model_version_id": self.model_id,
                "scene_id": "tn-scene",
                "idempotency_key": "inactive",
                "manifest": asset_manifest(),
            },
        )
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
