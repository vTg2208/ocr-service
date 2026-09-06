import unittest
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_models import FRAClaim, FRAGeometryVersion, RightsHolder
from app.db.fra_operational_models import ImageryArtifact, ImagerySceneRecord
from app.db.models import User
from app.services.satellite_ingestion import (
    COGRasterPreprocessor,
    SatelliteIngestionError,
    process_satellite_ingestion_job,
    request_satellite_ingestion,
)
from app.services.stac_imagery import SceneCandidate


GEOMETRY = {
    "type": "MultiPolygon",
    "coordinates": [[[[79.0, 10.0], [79.1, 10.0], [79.1, 10.1], [79.0, 10.1], [79.0, 10.0]]]],
}


def raster_bytes(value):
    data = np.full((100, 100), value, dtype="uint16")
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff", width=100, height=100, count=1, dtype=data.dtype,
            crs="EPSG:4326", transform=from_bounds(78.9, 9.9, 79.2, 10.2, 100, 100),
            nodata=0,
        ) as dataset:
            dataset.write(data, 1)
        return memory.read()


class Storage:
    def __init__(self):
        self.values = {}
        self.deleted = []

    def put(self, content, suffix):
        key = f"private/prepared-{len(self.values) + 1}{suffix}"
        self.values[key] = content
        return key

    def delete(self, key):
        self.deleted.append(key)
        self.values.pop(key, None)


class STAC:
    def search(self, geometry, date_range, collections, max_cloud):
        return [SceneCandidate(
            scene_id="S2-TN-PILOT", provider="earth-search.aws.element84.com",
            collection=collections[0], acquired_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
            footprint=GEOMETRY, cloud_cover=4.5, asset_keys=("green", "nir"),
            license_reference="https://sentinel.esa.int/terms",
            private_asset_references={
                "green": {"href": "https://rasters.test/green.tif", "type": "image/tiff"},
                "nir": {"href": "https://rasters.test/nir.tif", "type": "image/tiff"},
            },
        )]


class SatelliteIngestionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            user = User(external_id="satellite-owner", display_name="Owner", role="user")
            holder = RightsHolder(display_name="FRA holder", holder_type="individual")
            session.add_all([user, holder]); session.flush()
            claim = FRAClaim(
                claim_number="TN-SAT-1", right_type="IFR", status="submitted",
                rights_holder=holder, submitted_by=user.id,
            )
            session.add(claim); session.flush()
            session.add(FRAGeometryVersion(
                claim=claim, version=1, geometry=GEOMETRY, source="reviewed boundary",
                boundary_quality="surveyed", created_by=user.id,
            ))
            session.commit()
            self.user_id, self.claim_id = user.id, claim.id

    def tearDown(self):
        self.engine.dispose()

    @staticmethod
    def preprocessor(allowed=True):
        values = {
            "https://rasters.test/green.tif": raster_bytes(100),
            "https://rasters.test/nir.tif": raster_bytes(200),
        }

        @contextmanager
        def opener(href):
            with MemoryFile(values[href]) as memory:
                with memory.open() as dataset:
                    yield dataset

        return COGRasterPreprocessor(
            allowed_hosts={"rasters.test" if allowed else "other.test"},
            opener=opener, max_output_pixels=1_000_000,
        )

    def request(self, session, key="satellite-ingest-1"):
        return request_satellite_ingestion(
            session, session.get(FRAClaim, self.claim_id),
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 31),
            collection="sentinel-2-l2a", band_keys=["green", "nir"], max_cloud=20,
            actor_id=self.user_id, idempotency_key=key,
        )

    def test_real_raster_bands_are_clipped_stored_and_registered(self):
        storage = Storage()
        with Session(self.engine) as session:
            job = self.request(session)
            result = process_satellite_ingestion_job(
                session, job, stac_client=STAC(), preprocessor=self.preprocessor(),
                storage=storage,
            )
            self.assertEqual(result["status"], "completed")
            artifact = session.get(ImageryArtifact, uuid.UUID(result["artifact_id"]))
            scene = session.get(ImagerySceneRecord, artifact.imagery_scene_id)
            self.assertEqual(scene.scene_id, "S2-TN-PILOT")
            self.assertFalse(scene.synthetic)
            self.assertEqual(artifact.artifact_type, "analysis_ready_raster:2025-01-01:2025-01-31")
            self.assertEqual(artifact.statistics_json["band_keys"], ["green", "nir"])
            self.assertNotIn("rasters.test", str(artifact.provenance_json))
            with MemoryFile(storage.values[artifact.storage_key]) as memory, memory.open() as prepared:
                self.assertEqual(prepared.count, 2)
                self.assertEqual(prepared.crs.to_epsg(), 4326)
                self.assertLess(prepared.width * prepared.height, 100 * 100)
                self.assertEqual(prepared.descriptions, ("green", "nir"))

    def test_request_is_idempotent_and_disallowed_raster_host_fails_closed(self):
        with Session(self.engine) as session:
            self.assertEqual(self.request(session).id, self.request(session).id)
            with self.assertRaisesRegex(SatelliteIngestionError, "allow-listed"):
                process_satellite_ingestion_job(
                    session, self.request(session), stac_client=STAC(),
                    preprocessor=self.preprocessor(allowed=False), storage=Storage(),
                )


if __name__ == "__main__":
    unittest.main()
