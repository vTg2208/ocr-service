import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Claim, Document, Parcel, User
from app.services.conflict_detection import detect_conflicts


def geometry(x1, x2):
    return {"type": "MultiPolygon", "coordinates": [[[[x1, 0], [x2, 0], [x2, 1], [x1, 1], [x1, 0]]]]}


class SpatialConflictTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def claims(self, session, second_geometry, same_checksum=False):
        users = [User(external_id="a"), User(external_id="b")]
        session.add_all(users); session.flush()
        parcels = [
            Parcel(state="S", district="D", taluk="T", village="V", survey_number="1", subdivision_number="A", geometry=geometry(0, 1), source="test"),
            Parcel(state="S", district="D", taluk="T", village="V", survey_number="2", subdivision_number="A", geometry=second_geometry, source="test"),
        ]
        session.add_all(parcels); session.flush()
        docs = [
            Document(uploaded_by=users[0].id, storage_key="a", original_filename="a", content_type="image/png", sha256="a" * 64, ocr_status="completed", idempotency_key="a"),
            Document(uploaded_by=users[1].id, storage_key="b", original_filename="b", content_type="image/png", sha256=("a" if same_checksum else "b") * 64, ocr_status="completed", idempotency_key="b"),
        ]
        session.add_all(docs); session.flush()
        claims = [
            Claim(claimant_id=users[0].id, parcel_id=parcels[0].id, document_id=docs[0].id, status="matched", match_method="exact", idempotency_key="a"),
            Claim(claimant_id=users[1].id, parcel_id=parcels[1].id, document_id=docs[1].id, status="matched", match_method="exact", idempotency_key="b"),
        ]
        session.add_all(claims); session.flush()
        return claims

    def test_creates_spatial_conflict_above_both_thresholds(self):
        with Session(self.engine) as session:
            old, new = self.claims(session, geometry(.5, 1.5))
            conflicts = detect_conflicts(session, new, min_sqm=.1, min_percent=10)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].conflict_type, "spatial_overlap")
        self.assertEqual(float(conflicts[0].overlap_percent), 50)

    def test_ignores_negligible_sliver_below_threshold(self):
        with Session(self.engine) as session:
            old, new = self.claims(session, geometry(.999, 1.999))
            conflicts = detect_conflicts(session, new, min_sqm=.01, min_percent=1)
        self.assertEqual(conflicts, [])

    def test_same_checksum_creates_duplicate_document_conflict_without_identity_data(self):
        with Session(self.engine) as session:
            old, new = self.claims(session, geometry(2, 3), same_checksum=True)
            conflicts = detect_conflicts(session, new, min_sqm=.1, min_percent=10)
        self.assertEqual([item.conflict_type for item in conflicts], ["duplicate_document"])


if __name__ == "__main__":
    unittest.main()
