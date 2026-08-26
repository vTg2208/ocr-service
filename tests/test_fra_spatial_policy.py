import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_models import FRAClaim, FRAGeometryVersion, RightsHolder
from app.db.models import Parcel, User
from app.services.fra_spatial_policy import _area_sqm, evaluate_spatial_compatibility


OVERLAP_A = {
    "type": "MultiPolygon",
    "coordinates": [[[[79.0, 10.0], [79.001, 10.0], [79.001, 10.001], [79.0, 10.001], [79.0, 10.0]]]],
}
OVERLAP_B = {
    "type": "MultiPolygon",
    "coordinates": [[[[79.0005, 10.0005], [79.0015, 10.0005], [79.0015, 10.0015], [79.0005, 10.0015], [79.0005, 10.0005]]]],
}
DISJOINT = {
    "type": "MultiPolygon",
    "coordinates": [[[[80.0, 11.0], [80.001, 11.0], [80.001, 11.001], [80.0, 11.001], [80.0, 11.0]]]],
}


class FRASpatialPolicyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            user = User(external_id="spatial-reviewer", role="reviewer")
            holder = RightsHolder(display_name="Holder", holder_type="individual")
            session.add_all([user, holder])
            session.commit()
            self.user_id = user.id
            self.holder_id = holder.id

    def tearDown(self):
        self.engine.dispose()

    def _claim(self, session, number, right_type, *, geometry=None, status="submitted", parcel=None):
        claim = FRAClaim(
            claim_number=number,
            right_type=right_type,
            status=status,
            rights_holder_id=self.holder_id,
            submitted_by=self.user_id,
            parcel=parcel,
        )
        session.add(claim)
        session.flush()
        if geometry is not None:
            session.add(
                FRAGeometryVersion(
                    claim=claim,
                    version=1,
                    geometry=geometry,
                    source="test",
                    boundary_quality="unverified",
                    created_by=self.user_id,
                )
            )
            session.flush()
        return claim

    def test_sqlite_wgs84_area_is_measured_in_square_metres(self):
        area = _area_sqm(OVERLAP_A)
        self.assertGreater(area, 10_000)
        self.assertLess(area, 15_000)

    def test_ifr_overlapping_ifr_is_blocked(self):
        with Session(self.engine) as session:
            existing = self._claim(session, "IFR-A", "IFR", geometry=OVERLAP_A)
            candidate = self._claim(session, "IFR-B", "IFR", status="draft")
            result = evaluate_spatial_compatibility(
                session, candidate, OVERLAP_B, min_sqm=1, min_percent=1
            )

            self.assertEqual(result.outcome, "blocked")
            self.assertEqual(result.findings[0].related_claim_id, existing.id)
            self.assertGreater(result.findings[0].overlap_area_sqm, 1)

    def test_ifr_overlapping_cfr_requires_review_instead_of_blocking(self):
        with Session(self.engine) as session:
            self._claim(session, "CFR-A", "CFR", geometry=OVERLAP_A)
            candidate = self._claim(session, "IFR-B", "IFR", status="draft")
            result = evaluate_spatial_compatibility(
                session, candidate, OVERLAP_B, min_sqm=1, min_percent=1
            )

            self.assertEqual(result.outcome, "review_required")
            self.assertIn("layered", result.findings[0].reason.casefold())

    def test_community_claim_overlapping_any_right_requires_review(self):
        for existing_type in ("IFR", "CR", "CFR"):
            with self.subTest(existing_type=existing_type), Session(self.engine) as session:
                self._claim(session, f"{existing_type}-A", existing_type, geometry=OVERLAP_A)
                candidate = self._claim(session, f"CR-B-{existing_type}", "CR", status="draft")
                result = evaluate_spatial_compatibility(
                    session, candidate, OVERLAP_B, min_sqm=1, min_percent=1
                )
                self.assertEqual(result.outcome, "review_required")
                session.rollback()

    def test_rejected_claim_does_not_occupy_land(self):
        with Session(self.engine) as session:
            self._claim(session, "IFR-REJECTED", "IFR", geometry=OVERLAP_A, status="rejected")
            candidate = self._claim(session, "IFR-NEW", "IFR", status="draft")
            result = evaluate_spatial_compatibility(
                session, candidate, OVERLAP_B, min_sqm=1, min_percent=1
            )
            self.assertEqual(result.outcome, "allowed")
            self.assertEqual(result.findings, [])

    def test_disjoint_claim_is_allowed(self):
        with Session(self.engine) as session:
            self._claim(session, "IFR-A", "IFR", geometry=OVERLAP_A)
            candidate = self._claim(session, "IFR-B", "IFR", status="draft")
            result = evaluate_spatial_compatibility(
                session, candidate, DISJOINT, min_sqm=1, min_percent=1
            )
            self.assertEqual(result.outcome, "allowed")

    def test_same_parcel_ifr_is_blocked_without_geometry_overlap_calculation(self):
        with Session(self.engine) as session:
            parcel = Parcel(
                state="Tamil Nadu", district="Thanjavur", taluk="Kumbakonam",
                village="Example", survey_number="1", subdivision_number="A",
                geometry=OVERLAP_A, source="synthetic",
            )
            existing = self._claim(session, "IFR-A", "IFR", parcel=parcel)
            candidate = self._claim(session, "IFR-B", "IFR", status="draft", parcel=parcel)
            result = evaluate_spatial_compatibility(
                session, candidate, OVERLAP_B, min_sqm=1, min_percent=1
            )
            self.assertEqual(result.outcome, "blocked")
            self.assertEqual(result.findings[0].related_claim_id, existing.id)
            self.assertEqual(result.findings[0].reason, "same_parcel_exclusive_ifr")


if __name__ == "__main__":
    unittest.main()
