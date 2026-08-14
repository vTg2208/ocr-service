import io
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import jwt
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import patta_routes
from app.db.base import Base
from app.db.models import AuditEvent, Document, Parcel, User
from app.db.session import get_db
from app.main import app
from app.models.response_models import OCRResponse
from app.services.quality_assessment import assess_ocr_quality


GEOMETRY = {"type": "MultiPolygon", "coordinates": [[[[79.38, 10.96], [79.381, 10.96], [79.381, 10.961], [79.38, 10.961], [79.38, 10.96]]]]}


def png_bytes():
    content = io.BytesIO(); Image.new("RGB", (32, 24), "white").save(content, format="PNG")
    return content.getvalue()


def ocr_response():
    text = (
        "State: Tamil Nadu\nDistrict: Thanjavur\nTaluk: Kumbakonam\n"
        "Village: Example Village\nSurvey No. 701 / 4 b\nExtent: 0.12 hectares"
    )
    return OCRResponse(
        filename="patta.png", processing_time=0.1, text=text, confidence=91,
        quality=assess_ocr_quality(text, 91),
    )


class LandAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
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
        self.old_upload_dir = patta_routes.settings.secure_upload_dir
        patta_routes.settings.secure_upload_dir = self.temp.name
        with self.factory() as session:
            self.user = User(external_id="alice", display_name="Alice", role="user")
            self.other = User(external_id="bob", display_name="Bob", role="user")
            self.admin = User(external_id="admin", display_name="Admin", role="admin")
            self.parcel = Parcel(
                state="Tamil Nadu", district="Thanjavur", taluk="Kumbakonam",
                village="Example Village", survey_number="701", subdivision_number="4B",
                official_area_sqm=1200, geometry=GEOMETRY, source="Synthetic development data",
            )
            session.add_all([self.user, self.other, self.admin, self.parcel]); session.commit()
            self.user_id, self.other_id, self.admin_id, self.parcel_id = (
                self.user.id, self.other.id, self.admin.id, self.parcel.id,
            )
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        patta_routes.settings.secure_upload_dir = self.old_upload_dir
        self.temp.cleanup()

    def headers(self, external_id="alice", role="user", idem=None):
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "sub": external_id, "iat": now, "exp": now + timedelta(minutes=5),
                "iss": patta_routes.settings.auth_issuer,
                "aud": patta_routes.settings.auth_audience,
            }, patta_routes.settings.auth_secret,
            algorithm="HS256",
        )
        value = {"Authorization": f"Bearer {token}"}
        if idem: value["Idempotency-Key"] = idem
        return value

    def process(self, user="alice", idem="upload-1"):
        with patch("app.api.patta_routes.ocr_endpoint", new=AsyncMock(return_value=ocr_response())):
            return self.client.post(
                "/api/pattas/process", headers=self.headers(user, idem=idem),
                files={"file": ("patta.png", png_bytes(), "image/png")},
            )

    def create_conflict(self, prefix="review"):
        first_doc = self.process(idem=f"{prefix}-upload-a").json()["document_id"]
        fields = {"document_id": first_doc, "state": "Tamil Nadu", "district": "Thanjavur", "taluk": "Kumbakonam", "village": "Example Village", "survey_number": "701", "subdivision_number": "4B", "document_area_sqm": 1200}
        self.client.post("/api/parcels/resolve", headers=self.headers(), json=fields)
        self.client.post("/api/claims", headers=self.headers(idem=f"{prefix}-claim-a"), json={"document_id": first_doc, "parcel_id": str(self.parcel_id), "confirmed_fields": fields})
        second_doc = self.process(user="bob", idem=f"{prefix}-upload-b").json()["document_id"]
        fields["document_id"] = second_doc
        self.client.post("/api/parcels/resolve", headers=self.headers("bob"), json=fields)
        return self.client.post("/api/claims", headers=self.headers("bob", idem=f"{prefix}-claim-b"), json={"document_id": second_doc, "parcel_id": str(self.parcel_id), "confirmed_fields": fields})

    def test_requires_authentication(self):
        response = self.client.get(f"/api/parcels/{self.parcel_id}")
        self.assertEqual(response.status_code, 401)

    def test_rejects_tampered_authentication_token(self):
        response = self.client.get(
            f"/api/parcels/{self.parcel_id}", headers={"Authorization": "Bearer invalid"}
        )
        self.assertEqual(response.status_code, 401)

    def test_rejects_signed_token_without_expiry(self):
        token = jwt.encode(
            {
                "sub": "alice", "iat": datetime.now(timezone.utc),
                "iss": patta_routes.settings.auth_issuer,
                "aud": patta_routes.settings.auth_audience,
            },
            patta_routes.settings.auth_secret, algorithm="HS256",
        )
        response = self.client.get(
            f"/api/parcels/{self.parcel_id}", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 401)

    def test_process_persists_private_document_and_resolves_polygon_idempotently(self):
        first = self.process(); second = self.process()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["resolution"]["status"], "needs_confirmation")
        self.assertEqual(first.json()["resolution"]["parcel"]["geometry"], GEOMETRY)
        self.assertEqual(first.json()["document_id"], second.json()["document_id"])
        with self.factory() as session:
            document = session.scalar(select(Document))
            self.assertNotIn("uploads", document.storage_key)
            self.assertTrue(Path(self.temp.name, document.storage_key).is_file())

    def test_resolve_correction_then_two_users_submit_privacy_safe_conflict(self):
        first_doc = self.process().json()["document_id"]
        corrected = {
            "document_id": first_doc, "state": "Tamil Nadu", "district": "Thanjavur",
            "taluk": "Kumbakonam", "village": "Example Village", "survey_number": "701",
            "subdivision_number": "4B", "document_area_sqm": 1200,
        }
        resolution = self.client.post("/api/parcels/resolve", headers=self.headers(), json=corrected)
        self.assertEqual(resolution.json()["status"], "matched")
        claim_payload = {"document_id": first_doc, "parcel_id": str(self.parcel_id), "confirmed_fields": corrected}
        first_claim = self.client.post("/api/claims", headers=self.headers(idem="claim-a"), json=claim_payload)
        second_doc = self.process(user="bob", idem="upload-b").json()["document_id"]
        corrected["document_id"] = second_doc
        self.client.post("/api/parcels/resolve", headers=self.headers("bob"), json=corrected)
        claim_payload["document_id"] = second_doc; claim_payload["confirmed_fields"] = corrected
        second_claim = self.client.post("/api/claims", headers=self.headers("bob", idem="claim-b"), json=claim_payload)
        self.assertEqual(first_claim.json()["status"], "matched")
        self.assertEqual(second_claim.json()["status"], "conflicting")
        self.assertNotIn("claimant_id", second_claim.text)
        self.assertNotIn("document_id", second_claim.json()["conflicts"][0])

    def test_parcel_endpoint_never_returns_claimant_or_document(self):
        response = self.client.get(f"/api/parcels/{self.parcel_id}", headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("claimant", response.text)
        self.assertNotIn("document", response.text)

    def test_admin_conflicts_rejects_normal_user_and_allows_admin(self):
        denied = self.client.get("/api/admin/conflicts", headers=self.headers())
        allowed = self.client.get("/api/admin/conflicts", headers=self.headers("admin", "admin"))
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)

    def test_ui_is_served_with_accessible_workflow_and_explicit_map_container(self):
        response = self.client.get("/land-mapping")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="pattaFile"', response.text)
        self.assertIn('id="fieldForm"', response.text)
        self.assertIn('id="parcelMap"', response.text)
        self.assertIn('id="claimButton"', response.text)
        self.assertIn('/static/land-mapping/app.js?v=', response.text)

    def test_ui_static_assets_must_revalidate_after_a_deployment(self):
        response = self.client.get("/static/land-mapping/app.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-cache", response.headers.get("cache-control", ""))

    def test_conflict_creates_generic_notifications_for_both_users(self):
        self.create_conflict("notify")
        alice = self.client.get("/api/notifications/mine", headers=self.headers()).json()
        bob = self.client.get("/api/notifications/mine", headers=self.headers("bob")).json()
        self.assertEqual((len(alice), len(bob)), (1, 1))
        self.assertNotIn("Alice", bob[0]["message"])
        self.assertNotIn("Bob", alice[0]["message"])

    def test_admin_can_review_boundaries_evidence_and_audited_resolution_history(self):
        self.create_conflict("admin-review")
        headers = self.headers("admin", "admin")
        queue = self.client.get("/api/admin/conflicts", headers=headers).json()
        conflict = next(item for item in queue if item["type"] == "same_parcel")
        self.assertEqual(conflict["claim_a"]["parcel"]["geometry"], GEOMETRY)
        self.assertIn("survey_number", conflict["claim_b"]["evidence"])
        updated = self.client.patch(
            f"/api/admin/conflicts/{conflict['id']}", headers=headers,
            json={"status": "resolved", "resolution_notes": "Reviewed source records."},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["status"], "resolved")
        self.assertIsNotNone(updated.json()["resolved_at"])
        self.assertEqual(len(updated.json()["resolution_history"]), 1)
        with self.factory() as session:
            actions = list(session.scalars(select(AuditEvent.action)))
        self.assertIn("conflict_resolved", actions)


if __name__ == "__main__":
    unittest.main()
