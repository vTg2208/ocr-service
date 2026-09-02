import unittest
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from tests.test_fra_dashboards import seed_dashboard


class FRADashboardAPITests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool); Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        def override():
            with self.factory() as session: yield session
        app.dependency_overrides[get_db] = override
        with self.factory() as session: seed_dashboard(session); session.commit()
        self.client = TestClient(app)
    def tearDown(self): self.client.close(); app.dependency_overrides.clear(); self.engine.dispose()
    @staticmethod
    def headers(external_id):
        now = datetime.now(timezone.utc); token = jwt.encode({"sub": external_id, "iat": now, "exp": now + timedelta(minutes=5), "iss": settings.auth_issuer, "aud": settings.auth_audience}, settings.auth_secret, algorithm="HS256"); return {"Authorization": f"Bearer {token}"}

    def test_verifier_dashboard_requires_reviewer_and_planner_is_filtered_and_private(self):
        denied = self.client.get("/api/fra/dashboard/verifier", headers=self.headers("dashboard-staff")); self.assertEqual(denied.status_code, 403)
        verifier = self.client.get("/api/fra/dashboard/verifier", headers=self.headers("dashboard-reviewer")); self.assertEqual(verifier.status_code, 200, verifier.text)
        planner = self.client.get("/api/fra/dashboard/planner?district=District%20A", headers=self.headers("dashboard-staff")); self.assertEqual(planner.status_code, 200, planner.text)
        self.assertEqual(planner.json()["claims_by_status"], {"submitted": 1})
        self.assertNotIn("Private A", planner.text); self.assertNotIn("holder", planner.text.casefold())


if __name__ == "__main__": unittest.main()
