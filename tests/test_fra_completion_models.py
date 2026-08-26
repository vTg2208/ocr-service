import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import (
    AssetFeature,
    FRAArchiveRecord,
    FRAExtractionRun,
    FRAImportBatch,
    FRAVillageProfile,
    InferenceRun,
    ModelVersion,
    ProcessingJob,
)
from app.db.models import Document, User


VILLAGE_GEOMETRY = {
    "type": "MultiPolygon",
    "coordinates": [
        [[
            [79.0, 10.0],
            [79.01, 10.0],
            [79.01, 10.01],
            [79.0, 10.01],
            [79.0, 10.0],
        ]]
    ],
}
POINT = {"type": "Point", "coordinates": [79.005, 10.005]}


class FRACompletionModelTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _staff_and_document(self, session):
        staff = User(external_id="tn-staff-1", display_name="TN staff", role="reviewer")
        session.add(staff)
        session.flush()
        document = Document(
            uploaded_by=staff.id,
            storage_key="private://synthetic/tn-fra-1.pdf",
            original_filename="tn-fra-1.pdf",
            content_type="application/pdf",
            sha256="a" * 64,
            idempotency_key="tn-doc-1",
        )
        session.add(document)
        session.flush()
        return staff, document

    def _village(self):
        return FRAVillageProfile(
            state_code="TN",
            state_name="Tamil Nadu",
            district_code="TN-13",
            district_name="Thanjavur",
            block_code="TN-13-01",
            block_name="Kumbakonam",
            village_code="TN-13-01-001",
            village_name="Kottur",
            boundary=VILLAGE_GEOMETRY,
            tribal_groups_json=["Synthetic community"],
            socioeconomic_json={},
            provenance_json={"synthetic": True, "version": "demo-v1"},
            reference_version="demo-v1",
            synthetic=True,
        )

    def test_archive_retains_append_only_extraction_runs(self):
        with Session(self.engine) as session:
            staff, document = self._staff_and_document(session)
            batch = FRAImportBatch(
                source_label="Synthetic TN pack",
                state_code="TN",
                created_by=staff.id,
                idempotency_key="tn-batch-1",
                synthetic=True,
            )
            record = FRAArchiveRecord(
                batch=batch,
                document=document,
                legacy_reference="TN-FRA-1",
                state_code="TN",
                review_state="needs_review",
                synthetic=True,
            )
            record.extraction_runs.append(
                FRAExtractionRun(
                    raw_text="Form A",
                    standardized_json={},
                    field_evidence_json={},
                    overall_confidence=0.8,
                )
            )
            session.add(record)
            session.commit()

            self.assertEqual(len(record.extraction_runs), 1)
            self.assertEqual(record.extraction_runs[0].raw_text, "Form A")
            self.assertTrue(record.synthetic)

    def test_model_inference_and_asset_retain_version_provenance(self):
        with Session(self.engine) as session:
            admin = User(external_id="tn-admin-1", display_name="Admin", role="admin")
            village = self._village()
            session.add_all([admin, village])
            session.flush()
            model = ModelVersion(
                task="asset_detection",
                name="tn-assets",
                version="0.1.0",
                adapter_type="manifest",
                status="active",
                metrics_json={"status": "not_evaluated"},
                configuration_json={},
                label_map_json={},
                registered_by=admin.id,
            )
            run = InferenceRun(
                model_version=model,
                input_entity_type="village",
                input_entity_id=village.id,
                state="completed",
                input_json={},
                output_json={},
            )
            asset = AssetFeature(
                village=village,
                asset_class="water_body",
                point_geometry_json=POINT,
                source_type="model",
                inference_run=run,
                provenance_json={"synthetic": True},
                verification_state="unverified",
                synthetic=True,
            )
            session.add(asset)
            session.commit()

            self.assertEqual(asset.inference_run.model_version.version, "0.1.0")
            self.assertEqual(asset.point_geometry_json, POINT)
            self.assertTrue(asset.provenance_json["synthetic"])

    def test_natural_keys_are_declared_as_database_constraints(self):
        expected = {
            FRAImportBatch: {("created_by", "idempotency_key")},
            FRAArchiveRecord: {("batch_id", "legacy_reference")},
            ProcessingJob: {("task_type", "entity_id", "idempotency_key")},
            ModelVersion: {("task", "name", "version")},
            FRAVillageProfile: {
                ("state_code", "district_code", "block_code", "village_code")
            },
        }

        for model, required_constraints in expected.items():
            actual = {
                tuple(column.name for column in constraint.columns)
                for constraint in model.__table__.constraints
                if hasattr(constraint, "columns")
            }
            self.assertTrue(required_constraints <= actual, model.__name__)


if __name__ == "__main__":
    unittest.main()
