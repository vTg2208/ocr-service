import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app


class DemoAuthenticationTests(unittest.TestCase):
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
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def test_demo_login_sets_secure_browser_cookie_and_reports_session(self):
        login = self.client.post("/api/auth/demo-login", json={"access_code": "1234"})

        self.assertEqual(login.status_code, 200)
        cookie = login.headers["set-cookie"]
        self.assertIn("parcel_registry_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)

        session = self.client.get("/api/auth/session")
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json(), {
            "external_id": "registry-demo",
            "display_name": "Registry staff",
            "role": "user",
        })

    def test_demo_login_rejects_wrong_code_without_cookie(self):
        response = self.client.post("/api/auth/demo-login", json={"access_code": "9999"})

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("parcel_registry_session=", response.headers.get("set-cookie", ""))

    def test_logout_clears_session(self):
        self.client.post("/api/auth/demo-login", json={"access_code": "1234"})

        logout = self.client.post("/api/auth/logout")

        self.assertEqual(logout.status_code, 204)
        self.assertEqual(self.client.get("/api/auth/session").status_code, 401)


if __name__ == "__main__":
    unittest.main()
