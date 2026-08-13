"""Import a cadastral GeoJSON FeatureCollection into the configured database."""

import argparse
import json

from app.db.session import get_session_factory
from app.services.parcel_importer import import_geojson_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("geojson")
    args = parser.parse_args()
    with get_session_factory()() as session:
        report = import_geojson_file(args.geojson, session)
        session.commit()
    print(json.dumps(report.model_dump(), indent=2))


if __name__ == "__main__":
    main()
