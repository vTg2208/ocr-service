"""Run the production acquisition/model path without creating legal or user records."""

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

from shapely.geometry import MultiPolygon, box, mapping, shape

from app.config import get_settings
from app.services.land_cover import default_stac, discover_scene_pair
from app.services.land_cover_raster import (
    BAND_KEYS, MODEL_VERSION, SOURCE_REVISION, WEIGHTS_SHA256, DynamicWorldPredictor,
    LandCoverError, SentinelLandCoverReader, encode_preview, encode_raster, summarize,
)


def load_geometry(path, *, public_window=False):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("type") == "FeatureCollection":
        if len(payload.get("features", [])) != 1:
            raise ValueError("Choose one polygon feature for the bounded validation run.")
        payload = payload["features"][0]
    if payload.get("type") == "Feature":
        payload = payload["geometry"]
    geometry = shape(payload)
    if geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty or not geometry.is_valid:
        raise ValueError("Validation geometry must be a valid WGS84 polygon.")
    if public_window:
        centre = geometry.centroid
        geometry = box(centre.x-.002, centre.y-.002, centre.x+.002, centre.y+.002)
    if geometry.geom_type == "Polygon":
        geometry = MultiPolygon([geometry])
    return mapping(geometry)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--geometry", help="One WGS84 GeoJSON polygon/feature/collection")
    source.add_argument("--public-validation-window", action="store_true", help="Small study window around the bundled public village centroid; not a parcel")
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--output-directory", required=True, type=Path)
    args = parser.parse_args()
    if args.start > args.end or (args.end-args.start).days > 93 or args.end > datetime.now(timezone.utc).date():
        parser.error("Choose a past date range of at most 93 days.")
    destination = args.output_directory
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        parser.error("Choose an empty output directory to preserve previous validation artifacts.")
    settings = get_settings()
    model = DynamicWorldPredictor(settings.land_cover_weights_path)
    path = args.geometry or "data/real/arpisampalaiyam_village.geojson"
    geometry = load_geometry(path, public_window=args.public_validation_window)
    parameters = {"start_date": args.start.isoformat(), "end_date": args.end.isoformat(), "max_cloud": settings.land_cover_max_cloud}
    pair = discover_scene_pair(default_stac(), geometry, parameters)
    if pair is None:
        raise LandCoverError("No matching L1C/L2A quality pair in the bounded discovery result.")
    scene, quality = pair
    print(f"Reading real pixels from {scene.scene_id} with quality mask {quality.scene_id}...", flush=True)
    raw, inside, valid, profile = SentinelLandCoverReader(max_pixels=settings.land_cover_max_pixels).read(
        scene.private_asset_references, geometry, quality.private_asset_references["scl"])
    if int(valid.sum()) < 9:
        raise LandCoverError("Fewer than nine usable target pixel centres; no inference produced.")
    labels, summary, features = summarize(model.predict(raw), inside, valid, profile, geometry)
    artifacts = {
        "prepared-bands.tif": encode_raster(raw, profile, nodata=float("nan"), descriptions=BAND_KEYS),
        "validity.tif": encode_raster(inside.astype("uint8")+valid.astype("uint8"), profile, nodata=0),
        "labels.tif": encode_raster(labels, profile, nodata=255),
        "preview.png": encode_preview(labels),
    }
    for name, content in artifacts.items():
        (destination/name).write_bytes(content)
    (destination/"features.geojson").write_text(json.dumps(features), encoding="utf-8")
    report = {"validation_kind": "live_execution_only", "executed_at": datetime.now(timezone.utc).isoformat(),
        "not_a_parcel": args.public_validation_window, "geometry": geometry, "parameters": parameters,
        "model_version": MODEL_VERSION, "source_revision": SOURCE_REVISION, "weights_sha256": WEIGHTS_SHA256,
        "scene_id": scene.scene_id, "quality_scene_id": quality.scene_id, "acquired_at": scene.acquired_at.isoformat(),
        "runtime": model.runtime, "summary": summary,
        "artifact_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in artifacts.items()}}
    (destination/"validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Completed: {int(valid.sum())} usable pixels. Artifacts in {destination}. No ground-truth accuracy claimed.")


if __name__ == "__main__":
    main()
