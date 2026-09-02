import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import settings
from app.db.base import Base
from app.db.models import User
from app.db.session import get_db
from app.main import app


class MemoryStorage:
    def __init__(self):
        self.items = {}; self.deleted = []

    def put(self, content, suffix):
        key = f"private/geospatial-{len(self.items) + 1}{suffix}"; self.items[key] = content; return key

    def delete(self, key):
        self.deleted.append(key); self.items.pop(key, None)


class FRAGeospatialAPITests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        def override():
            with self.factory() as session: yield session
        app.dependency_overrides[get_db] = override
        with self.factory() as session:
            session.add_all([
                User(external_id="geo-api-user", display_name="Uploader", role="user"),
                User(external_id="geo-api-reviewer", display_name="Reviewer", role="reviewer"),
            ]); session.commit()
        self.client = TestClient(app); self.storage = MemoryStorage()

    def tearDown(self):
        self.client.close(); app.dependency_overrides.clear(); self.engine.dispose()

    @staticmethod
    def headers(user="geo-api-user"):
        now = datetime.now(timezone.utc)
        token = jwt.encode({"sub": user, "iat": now, "exp": now + timedelta(minutes=5), "iss": settings.auth_issuer, "aud": settings.auth_audience}, settings.auth_secret, algorithm="HS256")
        return {"Authorization": f"Bearer {token}", "Idempotency-Key": "geo-api-1"}

    def upload(self, *, crs="EPSG:4326", synthetic="false"):
        coordinates = [[[78.0, 11.0], [78.1, 11.0], [78.1, 11.1], [78.0, 11.0]]]
        data = {"type": "FeatureCollection", "features": [{"type": "Feature", "id": "pa-1", "properties": {"name": "Reference area"}, "geometry": {"type": "Polygon", "coordinates": coordinates}}]}
        return self.client.post(
            "/api/fra/geospatial/imports",
            headers=self.headers(),
            data={"dataset_kind": "protected_area", "source_authority": "Tamil Nadu Forest Department", "source_version": "2026-08", "declared_crs": crs, "synthetic": synthetic},
            files={"file": ("protected.geojson", io.BytesIO(json.dumps(data).encode()), "application/geo+json")},
        )

    def test_stage_preview_publish_and_privacy_boundaries(self):
        with patch("app.api.fra_geospatial_routes.create_storage", return_value=self.storage):
            staged = self.upload()
        self.assertEqual(staged.status_code, 202, staged.text)
        self.assertEqual(staged.json()["classification"], "declared_authoritative")
        import_id = staged.json()["id"]
        preview = self.client.get(f"/api/fra/geospatial/imports/{import_id}", headers=self.headers())
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["features"][0]["geometry"]["type"], "MultiPolygon")
        self.assertNotIn("storage_key", preview.text)
        denied = self.client.post(f"/api/fra/geospatial/imports/{import_id}/publish", headers=self.headers())
        self.assertEqual(denied.status_code, 403)
        published = self.client.post(f"/api/fra/geospatial/imports/{import_id}/publish", headers=self.headers("geo-api-reviewer"))
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(published.json()["state"], "published")

    def test_invalid_crs_cleans_private_upload(self):
        with patch("app.api.fra_geospatial_routes.create_storage", return_value=self.storage):
            response = self.upload(crs="EPSG:3857")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(self.storage.deleted), 1)


if __name__ == "__main__":
    unittest.main()
