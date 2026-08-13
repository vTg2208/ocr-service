import unittest
import json
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Parcel
from app.services.parcel_importer import import_geojson


def feature(survey="701", subdivision="4B", area=1200, geometry=None):
    return {
        "type": "Feature",
        "properties": {
            "state": " tamil   nadu ",
            "district": "THANJAVUR",
            "taluk": "Kumbakonam",
            "village": "Example Village",
            "survey_number": survey,
            "subdivision_number": subdivision,
            "official_area_sqm": area,
            "source": "Synthetic development data",
            "source_version": "2026-01",
            "source_record_id": f"{survey}-{subdivision}",
        },
        "geometry": geometry or {
            "type": "Polygon",
            "coordinates": [[[79.38, 10.96], [79.381, 10.96], [79.381, 10.961], [79.38, 10.961], [79.38, 10.96]]],
        },
    }


class ParcelImporterTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def test_imports_polygon_as_multipolygon_and_normalizes_key(self):
        with Session(self.engine) as session:
            report = import_geojson({"type": "FeatureCollection", "features": [feature()]}, session)
            session.commit()
            parcel = session.scalar(select(Parcel))

        self.assertEqual(report.inserted, 1)
        self.assertEqual(parcel.state, "Tamil Nadu")
        self.assertEqual(parcel.subdivision_number, "4B")
        self.assertEqual(parcel.geometry["type"], "MultiPolygon")

    def test_reimport_is_idempotent_and_updates_changed_source_record(self):
        with Session(self.engine) as session:
            first = import_geojson({"type": "FeatureCollection", "features": [feature()]}, session)
            session.commit()
            unchanged = import_geojson({"type": "FeatureCollection", "features": [feature()]}, session)
            changed = import_geojson({"type": "FeatureCollection", "features": [feature(area=1250)]}, session)
            session.commit()
            count = session.scalar(select(func.count()).select_from(Parcel))

        self.assertEqual((first.inserted, unchanged.skipped, changed.updated, count), (1, 1, 1, 1))

    def test_rejects_empty_or_non_polygon_and_counts_duplicate_input_keys(self):
        invalid = feature(geometry={"type": "Point", "coordinates": [79.38, 10.96]})
        duplicate = feature()
        with Session(self.engine) as session:
            report = import_geojson(
                {"type": "FeatureCollection", "features": [feature(), duplicate, invalid]}, session
            )
        self.assertEqual(report.inserted, 1)
        self.assertEqual(report.duplicate, 1)
        self.assertEqual(report.invalid, 1)

    def test_safely_repairs_self_intersecting_polygon_and_reports_it(self):
        bowtie = feature(geometry={
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]],
        })
        with Session(self.engine) as session:
            report = import_geojson({"type": "FeatureCollection", "features": [bowtie]}, session)
        self.assertEqual(report.inserted, 1)
        self.assertEqual(report.repaired, 1)

    def test_committed_development_seed_contains_and_imports_fifty_synthetic_parcels(self):
        payload = json.loads(Path("data/synthetic_example_village.geojson").read_text())
        with Session(self.engine) as session:
            report = import_geojson(payload, session)
            session.commit()
            acceptance = session.scalar(select(Parcel).where(
                Parcel.village == "Example Village", Parcel.survey_number == "701",
                Parcel.subdivision_number == "4B",
            ))
        self.assertEqual((len(payload["features"]), report.inserted), (50, 50))
        self.assertFalse(payload["metadata"]["authoritative"])
        self.assertIn("SYNTHETIC", acceptance.source)


if __name__ == "__main__":
    unittest.main()
