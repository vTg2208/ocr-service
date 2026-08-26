import unittest
from datetime import date

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_models import FRAClaim, FRAEvidenceItem, RightsHolder, SatelliteObservation
from app.db.models import User
from app.services.satellite_evidence import (
    ImageryRequest,
    ImageryScene,
    LocalManifestImageryProvider,
    LocalObservationAnalyser,
    SatelliteEvidenceValidationError,
    SatelliteProviderUnavailable,
    acquire_and_analyse,
    create_supporting_observations,
)


GEOMETRY = {
    "type": "MultiPolygon",
    "coordinates": [[[[79.0, 10.0], [79.001, 10.0], [79.001, 10.001], [79.0, 10.001], [79.0, 10.0]]]],
}


class UnavailableProvider:
    def acquire(self, request):
        raise SatelliteProviderUnavailable("Synthetic provider unavailable.")


class SatelliteEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            staff = User(external_id="satellite-staff", role="user")
            holder = RightsHolder(display_name="Ramu Naik", holder_type="individual")
            claim = FRAClaim(
                claim_number="IFR-SAT-1",
                right_type="IFR",
                status="submitted",
                rights_holder=holder,
                submitter=staff,
            )
            session.add(claim)
            session.commit()
            self.staff_id = staff.id
            self.claim_id = claim.id

    def tearDown(self):
        self.engine.dispose()

    @staticmethod
    def _scene(observations=None):
        return ImageryScene(
            scene_id="scene-2005",
            provider="local-manifest",
            source_uri="private://scene-2005",
            acquired_at=date(2005, 1, 15),
            metadata={
                "observations": observations
                or [
                    {
                        "asset_class": "agricultural_cover",
                        "value": 0.72,
                        "confidence": 0.83,
                    }
                ]
            },
        )

    def test_local_observation_creates_supporting_unverified_evidence(self):
        with Session(self.engine) as session:
            claim = session.get(FRAClaim, self.claim_id)
            observations = create_supporting_observations(
                session,
                claim,
                scene=self._scene(),
                geometry=GEOMETRY,
                analyser=LocalObservationAnalyser("local-v1"),
                actor_id=self.staff_id,
                request_id="sat-1",
            )
            session.commit()

            evidence = observations[0].evidence_item
            self.assertEqual(evidence.legal_role, "supporting")
            self.assertFalse(evidence.source_verified)
            self.assertEqual(evidence.verification_state, "unverified")
            self.assertNotIn("valid", evidence.description.casefold())
            self.assertEqual(observations[0].analyser_version, "local-v1")

    def test_provider_failure_creates_no_partial_records(self):
        with Session(self.engine) as session:
            claim = session.get(FRAClaim, self.claim_id)
            with self.assertRaises(SatelliteProviderUnavailable):
                acquire_and_analyse(
                    session,
                    claim,
                    request=ImageryRequest(scene_id="missing", geometry=GEOMETRY),
                    provider=UnavailableProvider(),
                    analyser=LocalObservationAnalyser("local-v1"),
                    actor_id=self.staff_id,
                    request_id="sat-2",
                )
            self.assertEqual(session.scalar(select(func.count()).select_from(SatelliteObservation)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(FRAEvidenceItem)), 0)

    def test_local_manifest_provider_returns_only_registered_scene(self):
        provider = LocalManifestImageryProvider({"scene-2005": self._scene()})
        scene = provider.acquire(ImageryRequest(scene_id="scene-2005", geometry=GEOMETRY))
        self.assertEqual(scene.source_uri, "private://scene-2005")
        with self.assertRaises(SatelliteProviderUnavailable):
            provider.acquire(ImageryRequest(scene_id="unknown", geometry=GEOMETRY))

    def test_automated_legal_conclusion_keys_are_rejected(self):
        scene = self._scene(
            observations=[
                {
                    "asset_class": "water_body",
                    "value": False,
                    "confidence": 0.91,
                    "valid": True,
                }
            ]
        )
        with Session(self.engine) as session:
            claim = session.get(FRAClaim, self.claim_id)
            with self.assertRaisesRegex(SatelliteEvidenceValidationError, "legal conclusion"):
                create_supporting_observations(
                    session,
                    claim,
                    scene=scene,
                    geometry=GEOMETRY,
                    analyser=LocalObservationAnalyser("local-v1"),
                    actor_id=self.staff_id,
                    request_id="sat-3",
                )
            self.assertEqual(session.scalar(select(func.count()).select_from(SatelliteObservation)), 0)

    def test_repeated_scene_asset_returns_existing_observation(self):
        with Session(self.engine) as session:
            claim = session.get(FRAClaim, self.claim_id)
            first = create_supporting_observations(
                session,
                claim,
                scene=self._scene(),
                geometry=GEOMETRY,
                analyser=LocalObservationAnalyser("local-v1"),
                actor_id=self.staff_id,
                request_id="sat-4",
            )
            session.flush()
            second = create_supporting_observations(
                session,
                claim,
                scene=self._scene(),
                geometry=GEOMETRY,
                analyser=LocalObservationAnalyser("local-v1"),
                actor_id=self.staff_id,
                request_id="sat-4-repeat",
            )
            self.assertEqual(first[0].id, second[0].id)
            self.assertEqual(session.scalar(select(func.count()).select_from(SatelliteObservation)), 1)


if __name__ == "__main__":
    unittest.main()
