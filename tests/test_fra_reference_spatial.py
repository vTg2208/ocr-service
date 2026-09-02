import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_operational_models import SpatialImportBatch, SpatialReferenceFeature
from app.db.models import User
from app.services.fra_reference_spatial import evaluate_reference_intersections


CANDIDATE = {
    "type": "MultiPolygon",
    "coordinates": [[[[79.0, 10.0], [79.002, 10.0], [79.002, 10.002], [79.0, 10.002], [79.0, 10.0]]]],
}
CONTAINING = {
    "type": "MultiPolygon",
    "coordinates": [[[[78.9, 9.9], [79.1, 9.9], [79.1, 10.1], [78.9, 10.1], [78.9, 9.9]]]],
}
OVERLAP = {
    "type": "MultiPolygon",
    "coordinates": [[[[79.001, 10.001], [79.003, 10.001], [79.003, 10.003], [79.001, 10.003], [79.001, 10.001]]]],
}


class FRAReferenceSpatialTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def add_reference(self, session, kind, record_id, geometry, *, published=True, version="tn-forest-2026"):
        user = session.query(User).first()
        if user is None:
            user = User(external_id="reference-loader", display_name="Loader", role="reviewer")
            session.add(user); session.flush()
        batch = SpatialImportBatch(
            dataset_kind=kind, source_authority="Tamil Nadu reference authority",
            source_version=version, state="published" if published else "staged",
            declared_crs="EPSG:4326", detected_crs="EPSG:4326",
            record_count=1, valid_count=1, synthetic=False, created_by=user.id,
            idempotency_key=f"{kind}-{record_id}", provenance_json={"classification": "published_authoritative_reference"},
        )
        feature = SpatialReferenceFeature(
            import_batch=batch, dataset_kind=kind,
            source_authority=batch.source_authority, source_version=version,
            source_record_id=record_id, geometry=geometry,
            properties_json={"name": record_id},
            provenance_json={"classification": "published_authoritative_reference"},
            published=published, synthetic=False,
        )
        session.add(feature); session.flush(); return feature

    def test_administrative_containment_and_reference_provenance(self):
        with Session(self.engine) as session:
            feature = self.add_reference(session, "administrative_boundary", "salem", CONTAINING, version="tn-admin-2026")
            findings = evaluate_reference_intersections(
                session, CANDIDATE, {"administrative_boundary"}, "fra-reference-v1"
            )
            self.assertEqual(findings[0].reference_feature_id, feature.id)
            self.assertEqual(findings[0].reason, "within_administrative_boundary")
            self.assertEqual(findings[0].outcome, "consistent")
            self.assertEqual(findings[0].reference_source_version, "tn-admin-2026")
            self.assertEqual(findings[0].policy_version, "fra-reference-v1")

    def test_protected_water_and_cadastral_intersections_are_non_blocking_review_findings(self):
        with Session(self.engine) as session:
            for kind in ("protected_area", "water_body", "cadastral_parcel"):
                self.add_reference(session, kind, kind, OVERLAP)
            findings = evaluate_reference_intersections(
                session, CANDIDATE,
                {"protected_area", "water_body", "cadastral_parcel"},
                "fra-reference-v1",
            )
            self.assertEqual(len(findings), 3)
            self.assertTrue(all(item.outcome == "review_required" for item in findings))
            self.assertTrue(all(item.overlap_area_sqm > 0 for item in findings))
            self.assertNotIn("blocked", {item.outcome for item in findings})

    def test_unpublished_reference_features_are_ignored(self):
        with Session(self.engine) as session:
            self.add_reference(session, "protected_area", "draft-pa", OVERLAP, published=False)
            self.assertEqual(
                evaluate_reference_intersections(
                    session, CANDIDATE, {"protected_area"}, "fra-reference-v1"
                ),
                [],
            )


if __name__ == "__main__":
    unittest.main()
