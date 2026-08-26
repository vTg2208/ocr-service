import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import ModelVersion
from app.db.models import User
from app.services.model_gateway import (
    ManifestAssetDetector,
    ManifestFRAEntityExtractor,
    ModelOutputValidationError,
    ModelRegistrationError,
    activate_model,
    register_model,
    validate_model_output,
)


class ModelGatewayTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_manifest_entity_adapter_emits_versioned_tamil_nadu_fields(self):
        result = ManifestFRAEntityExtractor("tn-manifest-v1").extract(
            "synthetic-1",
            {
                "village": "Kottur",
                "district": "Thanjavur",
                "right_type": "IFR",
            },
        )

        self.assertEqual(result.model_version, "tn-manifest-v1")
        self.assertEqual(result.fields["state"], "Tamil Nadu")
        self.assertEqual(result.fields["state_code"], "TN")
        self.assertTrue(result.provenance["synthetic"])

    def test_model_outputs_reject_legal_conclusion_keys_at_any_depth(self):
        for output in ({"approved": True}, {"result": {"sanctioned": False}}):
            with self.assertRaisesRegex(ModelOutputValidationError, "legal conclusion"):
                validate_model_output(output)

    def test_manifest_asset_adapter_validates_confidence_and_supporting_output(self):
        detector = ManifestAssetDetector("tn-assets-v1")
        result = detector.detect(
            "tn-scene-1",
            {"type": "MultiPolygon", "coordinates": []},
            {
                "synthetic": True,
                "features": [
                    {
                        "asset_class": "water_body",
                        "geometry": {"type": "Point", "coordinates": [79.0, 10.0]},
                        "value": {"present": True},
                        "confidence": 0.72,
                    }
                ],
            },
        )

        self.assertEqual(result.features[0]["asset_class"], "water_body")
        self.assertEqual(result.model_version, "tn-assets-v1")
        self.assertTrue(result.provenance["synthetic"])

    def test_register_and_activate_model_keeps_one_active_version_per_task(self):
        with Session(self.engine) as session:
            admin = User(external_id="model-admin", display_name="Admin", role="admin")
            session.add(admin)
            session.flush()
            first = register_model(
                session,
                task="asset_detection",
                name="tn-assets",
                version="0.1.0",
                adapter_type="manifest",
                actor_id=admin.id,
                metrics={"status": "not_evaluated"},
                configuration={"ready": True},
            )
            second = register_model(
                session,
                task="asset_detection",
                name="tn-assets",
                version="0.2.0",
                adapter_type="manifest",
                actor_id=admin.id,
                metrics={"status": "not_evaluated"},
                configuration={"ready": True},
            )
            activate_model(session, first)
            activate_model(session, second)
            session.commit()

            active = session.scalars(
                select(ModelVersion).where(
                    ModelVersion.task == "asset_detection",
                    ModelVersion.status == "active",
                )
            ).all()
            self.assertEqual(active, [second])

    def test_unready_model_cannot_be_activated(self):
        with Session(self.engine) as session:
            admin = User(external_id="unready-admin", display_name="Admin", role="admin")
            session.add(admin)
            session.flush()
            model = register_model(
                session,
                task="asset_detection",
                name="training-model",
                version="0.0.1",
                adapter_type="pytorch",
                actor_id=admin.id,
                metrics={"status": "not_evaluated"},
                configuration={"ready": False},
            )

            with self.assertRaisesRegex(ModelRegistrationError, "not ready"):
                activate_model(session, model)


if __name__ == "__main__":
    unittest.main()
