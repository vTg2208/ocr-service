import json
import io
import unittest
import zipfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import User
from app.services.fra_geospatial_import import (
    SpatialImportValidationError,
    FionaDatasetReader,
    publish_spatial_import,
    stage_spatial_import,
)


def feature(record_id, coordinates):
    return {
        "type": "Feature",
        "id": record_id,
        "properties": {"source_record_id": record_id, "name": record_id},
        "geometry": {"type": "Polygon", "coordinates": [coordinates]},
    }


class FRAGeospatialImportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory() as session:
            uploader = User(external_id="geo-user", display_name="Uploader", role="user")
            reviewer = User(external_id="geo-reviewer", display_name="Reviewer", role="reviewer")
            session.add_all([uploader, reviewer]); session.commit()
            self.uploader_id, self.reviewer_id = uploader.id, reviewer.id

    def tearDown(self):
        self.engine.dispose()

    def test_geojson_stage_repairs_polygon_deduplicates_and_keeps_provenance(self):
        square = [[78.0, 11.0], [78.1, 11.0], [78.1, 11.1], [78.0, 11.1], [78.0, 11.0]]
        bowtie = [[78.0, 11.0], [78.1, 11.1], [78.1, 11.0], [78.0, 11.1], [78.0, 11.0]]
        dataset = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "features": [feature("forest-1", square), feature("forest-1", square), feature("forest-2", bowtie)],
        }
        with self.factory() as session:
            batch = stage_spatial_import(
                session,
                content=json.dumps(dataset).encode(),
                filename="salem-forest.geojson",
                dataset_kind="forest_compartment",
                source_authority="Tamil Nadu Forest Department",
                source_version="2026-08",
                declared_crs="EPSG:4326",
                actor_id=self.uploader_id,
                idempotency_key="geo-stage-1",
                synthetic=False,
            )
            session.commit()
            self.assertEqual((batch.record_count, batch.valid_count), (3, 2))
            self.assertEqual((batch.duplicate_count, batch.repaired_count, batch.invalid_count), (1, 1, 0))
            self.assertEqual(len(batch.features), 2)
            self.assertTrue(all(item.geometry["type"] == "MultiPolygon" for item in batch.features))
            self.assertEqual(batch.provenance_json["classification"], "declared_authoritative")
            self.assertEqual(batch.features[0].provenance_json["source_version"], "2026-08")

    def test_invalid_or_mismatched_crs_is_rejected_before_staging(self):
        data = json.dumps({"type": "FeatureCollection", "features": []}).encode()
        with self.factory() as session:
            with self.assertRaisesRegex(SpatialImportValidationError, "CRS"):
                stage_spatial_import(
                    session,
                    content=data,
                    filename="unknown.geojson",
                    dataset_kind="administrative_boundary",
                    source_authority="Survey authority",
                    source_version="v1",
                    declared_crs="EPSG:3857",
                    actor_id=self.uploader_id,
                    idempotency_key="geo-stage-crs",
                    synthetic=False,
                )

    def test_shapefile_archive_rejects_traversal_before_optional_gdal_reader(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("../outside.shp", b"unsafe")
        with self.assertRaisesRegex(SpatialImportValidationError, "unsafe path"):
            FionaDatasetReader().read(content.getvalue(), "forest.zip")

    def test_publish_requires_reviewer_and_preserves_idempotent_stage(self):
        square = [[78.0, 11.0], [78.1, 11.0], [78.1, 11.1], [78.0, 11.0]]
        data = json.dumps({"type": "FeatureCollection", "features": [feature("v-1", square)]}).encode()
        with self.factory() as session:
            first = stage_spatial_import(
                session, content=data, filename="villages.geojson",
                dataset_kind="administrative_boundary", source_authority="Survey authority",
                source_version="v1", declared_crs="EPSG:4326", actor_id=self.uploader_id,
                idempotency_key="same-stage", synthetic=True,
            )
            second = stage_spatial_import(
                session, content=data, filename="changed.geojson",
                dataset_kind="administrative_boundary", source_authority="Survey authority",
                source_version="v1", declared_crs="EPSG:4326", actor_id=self.uploader_id,
                idempotency_key="same-stage", synthetic=True,
            )
            self.assertEqual(first.id, second.id)
            with self.assertRaises(PermissionError):
                publish_spatial_import(session, first, reviewer_id=self.uploader_id)
            publish_spatial_import(session, first, reviewer_id=self.reviewer_id)
            self.assertEqual(first.state, "published")
            self.assertTrue(first.features[0].published)
            self.assertEqual(first.provenance_json["classification"], "published_synthetic_reference")


if __name__ == "__main__":
    unittest.main()
