import unittest
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import settings
from app.db.base import Base
from app.db.models import User
from app.db.session import get_db
from app.main import app
from app.services.fra_atlas import import_village_profiles
from tests.test_fra_atlas import atlas_payload


class FRAAtlasAPITests(unittest.TestCase):
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
            admin = User(external_id="atlas-admin", display_name="Admin", role="admin")
            staff = User(external_id="atlas-staff", display_name="Staff", role="user")
            session.add_all([admin, staff])
            session.flush()
            import_village_profiles(session, atlas_payload(), actor_id=admin.id)
            session.commit()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    @staticmethod
    def headers(external_id="atlas-staff"):
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

    def test_atlas_requires_auth_and_returns_tamil_nadu_geojson(self):
        self.assertEqual(self.client.get("/api/fra/atlas/features").status_code, 401)
        response = self.client.get(
            "/api/fra/atlas/features?district=Thanjavur&layers=village",
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["type"], "FeatureCollection")
        self.assertEqual(len(response.json()["features"]), 2)
        self.assertTrue(all(item["properties"]["synthetic"] for item in response.json()["features"]))

    def test_atlas_summary_and_village_routes_share_filters(self):
        summary = self.client.get(
            "/api/fra/atlas/summary?district=Thanjavur&layers=village",
            headers=self.headers(),
        )
        villages = self.client.get(
            "/api/fra/villages?district=Thanjavur", headers=self.headers()
        )
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["village_count"], len(villages.json()["items"]))
        village_id = villages.json()["items"][0]["id"]
        detail = self.client.get(
            f"/api/fra/villages/{village_id}", headers=self.headers()
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["state_code"], "TN")
        self.assertIn("Synthetic demonstration data", detail.json()["warning"])

    def test_atlas_rejects_unsupported_state_and_invalid_layer(self):
        unsupported = self.client.get(
            "/api/fra/atlas/features?state=Odisha", headers=self.headers()
        )
        self.assertEqual(unsupported.status_code, 422)
        self.assertEqual(unsupported.json()["message"]["code"], "unsupported_state")
        invalid = self.client.get(
            "/api/fra/atlas/features?layers=legal_validity", headers=self.headers()
        )
        self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
    unittest.main()
