import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_operational_models import (
    DSSFactSnapshot,
    FRAIntakeItem,
    ImageryArtifact,
    ImagerySceneRecord,
    SchemeCatalogEntry,
    SpatialImportBatch,
    SpatialReferenceFeature,
)
from app.db.models import Claim, Document, Parcel, User


GEOMETRY = {
    "type": "MultiPolygon",
    "coordinates": [[[[78.0, 11.0], [78.01, 11.0], [78.01, 11.01], [78.0, 11.01], [78.0, 11.0]]]],
}


class FRAOperationalModelTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _legacy_claim(self, session):
        user = User(external_id="operational-user", display_name="TN staff", role="reviewer")
        parcel = Parcel(
            state="Tamil Nadu", district="Salem", taluk="Yercaud", village="Kottur",
            survey_number="12", subdivision_number="A", geometry=GEOMETRY,
            source="test", source_version="v1", source_record_id="parcel-12-a",
        )
        session.add_all([user, parcel]); session.flush()
        document = Document(
            uploaded_by=user.id, storage_key="private/test/claim.pdf",
            original_filename="claim.pdf", content_type="application/pdf",
            sha256="a" * 64, idempotency_key="document-1",
        )
        session.add(document); session.flush()
        claim = Claim(
            claimant_id=user.id, parcel_id=parcel.id, document_id=document.id,
            match_method="exact", idempotency_key="claim-1",
        )
        session.add(claim); session.flush()
        return user, claim

    def test_one_intake_item_is_allowed_per_legacy_claim(self):
        with Session(self.engine) as session:
            user, claim = self._legacy_claim(session)
            session.add(FRAIntakeItem(
                legacy_claim_id=claim.id, state="awaiting_triage", created_by=user.id,
            ))
            session.commit()
            session.add(FRAIntakeItem(
                legacy_claim_id=claim.id, state="awaiting_triage", created_by=user.id,
            ))

            with self.assertRaises(IntegrityError):
                session.commit()

    def test_operational_models_declare_required_natural_keys(self):
        expected = {
            FRAIntakeItem: {("legacy_claim_id",)},
            SpatialImportBatch: {("created_by", "idempotency_key")},
            SpatialReferenceFeature: {
                ("source_authority", "source_version", "source_record_id")
            },
            ImagerySceneRecord: {("provider", "collection", "scene_id")},
            ImageryArtifact: {("claim_id", "geometry_version_id", "artifact_type", "processor_version")},
            DSSFactSnapshot: {("claim_id", "derivation_version", "idempotency_key")},
            SchemeCatalogEntry: {("scheme_code", "version")},
        }

        for model, required in expected.items():
            actual = {
                tuple(column.name for column in constraint.columns)
                for constraint in model.__table__.constraints
                if hasattr(constraint, "columns")
            }
            self.assertTrue(required <= actual, model.__name__)

    def test_scene_and_reference_provenance_survive_round_trip(self):
        with Session(self.engine) as session:
            user, _claim = self._legacy_claim(session)
            batch = SpatialImportBatch(
                dataset_kind="protected_area", source_authority="Tamil Nadu test authority",
                source_version="2026-01", state="staged", created_by=user.id,
                idempotency_key="spatial-1", provenance_json={"license": "test-only"},
            )
            feature = SpatialReferenceFeature(
                import_batch=batch, dataset_kind="protected_area",
                source_authority="Tamil Nadu test authority", source_version="2026-01",
                source_record_id="pa-1", geometry=GEOMETRY,
                properties_json={"name": "Test reserve"}, provenance_json={"reviewed": True},
            )
            scene = ImagerySceneRecord(
                provider="test-stac", collection="landsat-c2-l2", scene_id="scene-2005",
                acquired_at=datetime(2005, 1, 15, tzinfo=timezone.utc),
                footprint=GEOMETRY, cloud_cover=12.5,
                asset_references_json={"red": "private://scene/red.tif"},
                provenance_json={"license": "test-only"},
            )
            session.add_all([feature, scene]); session.commit()

            self.assertEqual(feature.import_batch.provenance_json["license"], "test-only")
            self.assertEqual(scene.asset_references_json["red"], "private://scene/red.tif")


if __name__ == "__main__":
    unittest.main()
