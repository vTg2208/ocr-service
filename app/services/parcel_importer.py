"""Idempotent GeoJSON cadastral parcel importer."""

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.validation import make_valid
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Parcel
from app.services.parcel_normalization import normalize_admin_name, normalize_identifier


@dataclass
class ImportReport:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    invalid: int = 0
    duplicate: int = 0
    repaired: int = 0
    errors: list[str] = field(default_factory=list)

    def model_dump(self) -> dict:
        return asdict(self)


def _multi_polygon(raw_geometry: dict) -> tuple[dict, bool]:
    if not raw_geometry or raw_geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Geometry must be a Polygon or MultiPolygon.")
    geometry = shape(raw_geometry)
    if geometry.is_empty:
        raise ValueError("Geometry must not be empty.")
    repaired = False
    if not geometry.is_valid:
        geometry = make_valid(geometry)
        repaired = True
    if isinstance(geometry, Polygon):
        geometry = MultiPolygon([geometry])
    elif not isinstance(geometry, MultiPolygon):
        polygons = [part for part in getattr(geometry, "geoms", []) if isinstance(part, Polygon)]
        if not polygons:
            raise ValueError("Geometry repair did not produce a polygon.")
        geometry = MultiPolygon(polygons)
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError("Geometry is invalid and could not be safely repaired.")
    # Shapely mappings contain tuples; JSON databases round-trip them as lists.
    return json.loads(json.dumps(mapping(geometry))), repaired


def _normalized_properties(properties: dict) -> dict:
    required = ("state", "district", "taluk", "village", "survey_number", "source")
    missing = [field for field in required if not str(properties.get(field, "")).strip()]
    if missing:
        raise ValueError(f"Missing required properties: {', '.join(missing)}")
    try:
        area = (
            Decimal(str(properties["official_area_sqm"]))
            if properties.get("official_area_sqm") is not None
            else None
        )
    except InvalidOperation as exc:
        raise ValueError("official_area_sqm must be numeric.") from exc
    return {
        "state": normalize_admin_name(properties["state"]),
        "district": normalize_admin_name(properties["district"]),
        "taluk": normalize_admin_name(properties["taluk"]),
        "village": normalize_admin_name(properties["village"]),
        "survey_number": normalize_identifier(properties["survey_number"]),
        "subdivision_number": normalize_identifier(properties.get("subdivision_number", "")),
        "official_area_sqm": area,
        "source": str(properties["source"]).strip(),
        "source_version": properties.get("source_version"),
        "source_record_id": properties.get("source_record_id"),
        "boundary_quality": properties.get("boundary_quality", "unknown"),
    }


def _key(values: dict) -> tuple[str, ...]:
    return tuple(values[field] for field in (
        "state", "district", "taluk", "village", "survey_number", "subdivision_number"
    ))


def _same(existing: Parcel, values: dict, geometry: dict) -> bool:
    fields = ("official_area_sqm", "source", "source_version", "source_record_id", "boundary_quality")
    def comparable(value):
        return float(value) if isinstance(value, Decimal) else value
    return all(
        comparable(getattr(existing, field)) == comparable(values[field]) for field in fields
    ) and existing.geometry == geometry


def import_geojson(payload: dict, session: Session) -> ImportReport:
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise ValueError("Input must be a GeoJSON FeatureCollection.")
    report = ImportReport()
    seen: set[tuple[str, ...]] = set()
    for index, feature in enumerate(payload["features"]):
        try:
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise ValueError("Record must be a GeoJSON Feature.")
            values = _normalized_properties(feature.get("properties") or {})
            geometry, repaired = _multi_polygon(feature.get("geometry"))
            key = _key(values)
            if key in seen:
                report.duplicate += 1
                continue
            seen.add(key)
            existing = session.scalar(select(Parcel).where(
                Parcel.state == key[0], Parcel.district == key[1], Parcel.taluk == key[2],
                Parcel.village == key[3], Parcel.survey_number == key[4],
                Parcel.subdivision_number == key[5],
            ))
            if existing is None:
                session.add(Parcel(**values, geometry=geometry))
                report.inserted += 1
            elif _same(existing, values, geometry):
                report.skipped += 1
            else:
                for field, value in values.items():
                    setattr(existing, field, value)
                existing.geometry = geometry
                report.updated += 1
            if repaired:
                report.repaired += 1
            session.flush()
        except (TypeError, ValueError) as exc:
            report.invalid += 1
            report.errors.append(f"Feature {index}: {exc}")
    return report


def import_geojson_file(path: str | Path, session: Session) -> ImportReport:
    with Path(path).open(encoding="utf-8") as source:
        return import_geojson(json.load(source), session)
