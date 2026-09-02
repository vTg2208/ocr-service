import unittest
import uuid
import base64
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.fra_completion_models import ModelVersion
from app.db.fra_models import FRAClaim, FRAGeometryVersion, RightsHolder
from app.db.fra_operational_models import ImageryArtifact, ImagerySceneRecord
from app.db.models import User
from app.services.historical_evidence import (
    HistoricalEvidenceError,
    HistoricalProcessingResult,
    create_historical_processor,
    process_historical_evidence_job,
    request_historical_evidence,
)
from app.services.stac_imagery import SceneCandidate, STACProviderError


GEOMETRY = {"type": "MultiPolygon", "coordinates": [[[[79, 10], [79.1, 10], [79.1, 10.1], [79, 10], [79, 10]]]]}


class MemoryStorage:
    def __init__(self): self.values = {}; self.deleted = []
    def put(self, content, suffix): key = f"private/artifact-{len(self.values)+1}{suffix}"; self.values[key] = content; return key
    def delete(self, key): self.deleted.append(key); self.values.pop(key, None)


class YearSTAC:
    def __init__(self, fail_year=None): self.fail_year = fail_year
    def search(self, geometry, date_range, collections, max_cloud):
        year = date_range[0].year
        if year == self.fail_year: raise STACProviderError("provider unavailable")
        return [SceneCandidate(
            scene_id=f"scene-{year}", provider="stac.test", collection=collections[0],
            acquired_at=datetime(year, 6, 1, tzinfo=timezone.utc), footprint=GEOMETRY,
            cloud_cover=8, asset_keys=("visual",), license_reference="https://example.org/license",
            private_asset_references={"visual": {"href": "https://data.test/a.tif?secret=x"}},
        )]


class Processor:
    version = "historical-v1"
    def __init__(self, fail_year=None, result_version=None): self.fail_year = fail_year; self.result_version = result_version
    def process(self, scene, geometry, target_year):
        if target_year == self.fail_year: raise HistoricalEvidenceError("processor unavailable", retriable=True)
        return HistoricalProcessingResult(
            content=f"artifact-{target_year}".encode(), statistics={"forest_index": 0.62},
            quality_flags=["supporting_observation"],
            processor_version=self.result_version or self.version, model_version="model-1",
        )


class HistoricalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory() as session:
            user = User(external_id="history-user", display_name="Staff", role="user")
            holder = RightsHolder(display_name="Ramu", holder_type="individual")
            session.add_all([user, holder]); session.flush()
            claim = FRAClaim(claim_number="TN-HIST-1", right_type="IFR", status="submitted", rights_holder_id=holder.id, submitted_by=user.id)
            session.add(claim); session.flush()
            geometry = FRAGeometryVersion(claim=claim, version=1, geometry=GEOMETRY, source="survey", boundary_quality="surveyed", created_by=user.id)
            model = ModelVersion(task="historical_evidence", adapter_type="rest", name="history", version="historical-v1", status="active", configuration_json={"ready": True}, label_map_json={}, metrics_json={}, registered_by=user.id)
            session.add_all([geometry, model]); session.commit()
            self.user_id, self.claim_id, self.model_id = user.id, claim.id, model.id

    def tearDown(self): self.engine.dispose()

    def job(self, session, years=(2005,), key="history-1"):
        claim = session.get(FRAClaim, self.claim_id)
        return request_historical_evidence(session, claim, target_years=list(years), actor_id=self.user_id, idempotency_key=key)

    def test_request_is_idempotent_and_missing_processor_returns_insufficient_model(self):
        with self.factory() as session:
            first = self.job(session); second = self.job(session)
            self.assertEqual(first.id, second.id)
            result = process_historical_evidence_job(session, first, stac_client=YearSTAC(), processor=None, storage=MemoryStorage(), model=None)
            self.assertEqual(result, {"status": "insufficient_model", "artifact_ids": []})

    def test_processing_persists_scene_checksum_and_private_artifact_reference(self):
        storage = MemoryStorage()
        with self.factory() as session:
            job = self.job(session, years=(2004, 2005))
            model = session.get(ModelVersion, self.model_id)
            result = process_historical_evidence_job(session, job, stac_client=YearSTAC(), processor=Processor(), storage=storage, model=model)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(result["artifact_ids"]), 2)
            artifacts = session.scalars(select(ImageryArtifact).order_by(ImageryArtifact.target_year)).all()
            self.assertEqual([item.target_year for item in artifacts], [2004, 2005])
            self.assertTrue(all(len(item.content_sha256) == 64 for item in artifacts))
            self.assertTrue(all(item.storage_key.startswith("private/") for item in artifacts))
            self.assertEqual(session.scalar(select(func.count(ImagerySceneRecord.id))), 2)

    def test_version_mismatch_and_provider_failure_leave_no_partial_artifacts_or_files(self):
        for processor, stac in ((Processor(result_version="wrong"), YearSTAC()), (Processor(), YearSTAC(fail_year=2005))):
            with self.subTest(processor=processor, stac=stac), self.factory() as session:
                storage = MemoryStorage(); job = self.job(session, years=(2004, 2005), key=str(uuid.uuid4()))
                model = session.get(ModelVersion, self.model_id)
                with self.assertRaises(HistoricalEvidenceError):
                    process_historical_evidence_job(session, job, stac_client=stac, processor=processor, storage=storage, model=model)
                self.assertEqual(session.scalar(select(func.count(ImageryArtifact.id))), 0)
                self.assertEqual(storage.values, {})

    def test_rest_processor_is_attachable_and_validates_its_version(self):
        calls = []
        with self.factory() as session:
            model = session.get(ModelVersion, self.model_id)
            model.configuration_json = {
                "ready": True,
                "endpoint": "https://models.example.org/historical",
                "allowed_hosts": ["models.example.org"],
                "timeout_seconds": 15,
            }

            def transport(endpoint, payload, timeout):
                calls.append((endpoint, payload, timeout))
                return {
                    "artifact_base64": base64.b64encode(b"historical-observation").decode(),
                    "statistics": {"forest_index": 0.62},
                    "quality_flags": ["cloud_screened"],
                    "processor_version": "historical-v1",
                    "model_version": "historical-v1",
                    "provenance": {"method": "attached_rest_model"},
                }

            processor = create_historical_processor(model, rest_transport=transport)
            result = processor.process(YearSTAC().search(GEOMETRY, (datetime(2005, 1, 1).date(), datetime(2005, 12, 31).date()), ["landsat-c2-l2"], 40)[0], GEOMETRY, 2005)
            self.assertEqual(result.content, b"historical-observation")
            self.assertEqual(calls[0][0], "https://models.example.org/historical")
            self.assertNotIn("secret=x", repr(processor))


if __name__ == "__main__": unittest.main()
