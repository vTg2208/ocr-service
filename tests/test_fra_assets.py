import unittest
import uuid

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import AssetFeature, FRAVillageProfile, InferenceRun, ModelVersion
from app.db.models import User
from app.services.fra_assets import (
    AssetReviewConflict,
    AssetValidationError,
    enqueue_asset_inference,
    process_asset_inference,
    review_asset,
)
from app.services.model_gateway import ManifestAssetDetector
from app.services.processing_jobs import run_one_job


BOUNDARY = {
    "type": "MultiPolygon",
    "coordinates": [[[[79.1, 10.7], [79.12, 10.7], [79.12, 10.72], [79.1, 10.72], [79.1, 10.7]]]],
}


def asset_manifest():
    return {
        "synthetic": True,
        "acquired_at": "2005-01-15",
        "features": [
            {
                "asset_class": "forest_cover",
                "geometry": BOUNDARY,
                "value": {"coverage_fraction": 0.62},
                "confidence": 0.81,
            },
            {
                "asset_class": "water_body",
                "geometry": {"type": "Point", "coordinates": [79.11, 10.71]},
                "value": {"present": True},
                "confidence": 0.74,
            },
        ],
    }


class FRAAssetTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _seed(self, session):
        staff = User(external_id=f"asset-staff-{uuid.uuid4()}", display_name="Staff", role="user")
        reviewer = User(
            external_id=f"asset-reviewer-{uuid.uuid4()}", display_name="Reviewer", role="reviewer"
        )
        village = FRAVillageProfile(
            state_code="TN",
            state_name="Tamil Nadu",
            district_code="TN-13",
            district_name="Thanjavur",
            block_code="TN-13-01",
            block_name="Kumbakonam",
            village_code="TN-13-01-001",
            village_name="Kottur Demo",
            boundary=BOUNDARY,
            tribal_groups_json=[],
            socioeconomic_json={},
            provenance_json={"synthetic": True},
            reference_version="demo-v1",
            synthetic=True,
        )
        session.add_all([staff, reviewer, village])
        session.flush()
        model = ModelVersion(
            task="asset_detection",
            name="tn-assets",
            version="0.1.0",
            adapter_type="manifest",
            status="active",
            label_map_json={},
            metrics_json={"status": "not_evaluated"},
            configuration_json={"ready": True},
            registered_by=reviewer.id,
        )
        session.add(model)
        session.flush()
        return staff, reviewer, village, model

    def test_manifest_inference_creates_unverified_supporting_assets(self):
        with Session(self.engine) as session:
            staff, _reviewer, village, model = self._seed(session)
            job = enqueue_asset_inference(
                session,
                village_id=village.id,
                claim_id=None,
                model_version_id=model.id,
                scene_id="tn-scene-2005",
                actor_id=staff.id,
                idempotency_key="asset-1",
                manifest=asset_manifest(),
            )
            assets = process_asset_inference(
                session, job, adapter=ManifestAssetDetector("0.1.0")
            )
            session.commit()

            self.assertEqual({asset.asset_class for asset in assets}, {"forest_cover", "water_body"})
            self.assertTrue(all(asset.verification_state == "unverified" for asset in assets))
            self.assertTrue(all(asset.provenance_json["synthetic"] for asset in assets))
            self.assertTrue(all(asset.inference_run.model_version_id == model.id for asset in assets))
            self.assertEqual(
                session.scalar(select(func.count()).select_from(InferenceRun)), 1
            )

    def test_reviewer_correction_supersedes_without_overwriting_model_output(self):
        with Session(self.engine) as session:
            staff, reviewer, village, model = self._seed(session)
            job = enqueue_asset_inference(
                session,
                village_id=village.id,
                claim_id=None,
                model_version_id=model.id,
                scene_id="tn-scene-2005",
                actor_id=staff.id,
                idempotency_key="asset-2",
                manifest=asset_manifest(),
            )
            asset = process_asset_inference(
                session, job, adapter=ManifestAssetDetector("0.1.0")
            )[1]
            original = dict(asset.observed_value_json)
            corrected = review_asset(
                session,
                asset,
                outcome="corrected",
                reviewer_id=reviewer.id,
                corrected_value={"present": False},
                reasons=["Synthetic field visit TN-1"],
                expected_revision=0,
            )
            session.commit()

            self.assertEqual(corrected.supersedes_id, asset.id)
            self.assertEqual(asset.verification_state, "superseded")
            self.assertEqual(asset.observed_value_json, original)
            self.assertEqual(corrected.verification_state, "verified")
            self.assertEqual(corrected.source_type, "manual_correction")

    def test_stale_or_unauthorized_review_does_not_change_asset(self):
        with Session(self.engine) as session:
            staff, reviewer, village, model = self._seed(session)
            job = enqueue_asset_inference(
                session,
                village_id=village.id,
                claim_id=None,
                model_version_id=model.id,
                scene_id="tn-scene-2005",
                actor_id=staff.id,
                idempotency_key="asset-3",
                manifest=asset_manifest(),
            )
            asset = process_asset_inference(
                session, job, adapter=ManifestAssetDetector("0.1.0")
            )[0]
            with self.assertRaises(PermissionError):
                review_asset(
                    session,
                    asset,
                    outcome="verified",
                    reviewer_id=staff.id,
                    reasons=[],
                    expected_revision=0,
                )
            with self.assertRaises(AssetReviewConflict):
                review_asset(
                    session,
                    asset,
                    outcome="verified",
                    reviewer_id=reviewer.id,
                    reasons=[],
                    expected_revision=2,
                )
            self.assertEqual(asset.verification_state, "unverified")

    def test_inactive_model_and_legal_manifest_are_rejected_without_runs(self):
        with Session(self.engine) as session:
            staff, _reviewer, village, model = self._seed(session)
            job = enqueue_asset_inference(
                session,
                village_id=village.id,
                claim_id=None,
                model_version_id=model.id,
                scene_id="tn-scene-2005",
                actor_id=staff.id,
                idempotency_key="asset-4",
                manifest=asset_manifest(),
            )
            model.status = "inactive"
            with self.assertRaisesRegex(AssetValidationError, "active"):
                process_asset_inference(
                    session, job, adapter=ManifestAssetDetector("0.1.0")
                )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(InferenceRun)), 0
            )

    def test_registered_worker_handler_completes_asset_job(self):
        with Session(self.engine, expire_on_commit=False) as session:
            staff, _reviewer, village, model = self._seed(session)
            job = enqueue_asset_inference(
                session,
                village_id=village.id,
                claim_id=None,
                model_version_id=model.id,
                scene_id="tn-scene-2005",
                actor_id=staff.id,
                idempotency_key="asset-worker-1",
                manifest=asset_manifest(),
            )
            session.commit()

            completed = run_one_job(session, worker_id="asset-test-worker")

            self.assertEqual(completed.id, job.id)
            self.assertEqual(completed.state, "completed")
            self.assertEqual(len(completed.result_json["asset_ids"]), 2)


if __name__ == "__main__":
    unittest.main()
