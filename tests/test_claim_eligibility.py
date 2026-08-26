import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Claim, Document, Parcel, User
from app.services.claim_eligibility import ClaimUnavailableError, ensure_land_available


def polygon(x1=0, y1=0, x2=1, y2=1):
    return {
        "type": "MultiPolygon",
        "coordinates": [[[[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]]],
    }


class ClaimEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def _claimed_parcel(self, session, geometry=None):
        user = User(external_id="existing-user", role="user")
        parcel = Parcel(
            state="Tamil Nadu", district="Demo", taluk="Demo", village="Demo",
            survey_number="1", subdivision_number="A", official_area_sqm=100,
            geometry=geometry or polygon(), source="Synthetic test data",
        )
        session.add_all([user, parcel]); session.flush()
        document = Document(
            uploaded_by=user.id, storage_key="private/existing", original_filename="patta.png",
            content_type="image/png", sha256="a" * 64, ocr_status="completed",
            idempotency_key="existing-document",
        )
        session.add(document); session.flush()
        claim = Claim(
            claimant_id=user.id, parcel_id=parcel.id, document_id=document.id,
            confirmed_fields_json={}, status="matched", match_method="exact",
            idempotency_key="existing-claim",
        )
        session.add(claim); session.flush()
        return parcel, claim

    def test_same_parcel_is_unavailable(self):
        with Session(self.engine) as session:
            parcel, claim = self._claimed_parcel(session)
            with self.assertRaises(ClaimUnavailableError) as raised:
                ensure_land_available(session, parcel.id)
        self.assertEqual(raised.exception.reason, "same_parcel")
        self.assertEqual(raised.exception.blocking_claim_id, claim.id)

    def test_overlapping_parcel_is_unavailable(self):
        with Session(self.engine) as session:
            self._claimed_parcel(session, polygon(0, 0, 2, 2))
            candidate = Parcel(
                state="Tamil Nadu", district="Demo", taluk="Demo", village="Demo",
                survey_number="2", subdivision_number="A", official_area_sqm=100,
                geometry=polygon(1, 1, 3, 3), source="Synthetic test data",
            )
            session.add(candidate); session.flush()
            with self.assertRaises(ClaimUnavailableError) as raised:
                ensure_land_available(session, candidate.id, min_sqm=.1, min_percent=1)
        self.assertEqual(raised.exception.reason, "spatial_overlap")

    def test_touching_boundary_does_not_block_claim(self):
        with Session(self.engine) as session:
            self._claimed_parcel(session, polygon(0, 0, 1, 1))
            candidate = Parcel(
                state="Tamil Nadu", district="Demo", taluk="Demo", village="Demo",
                survey_number="2", subdivision_number="A", official_area_sqm=100,
                geometry=polygon(1, 0, 2, 1), source="Synthetic test data",
            )
            session.add(candidate); session.flush()
            self.assertIsNone(ensure_land_available(session, candidate.id, min_sqm=.1, min_percent=1))


if __name__ == "__main__":
    unittest.main()
