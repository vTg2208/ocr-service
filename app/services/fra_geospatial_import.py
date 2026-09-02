"""Staging, repair, provenance, and publication of FRA reference vectors."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Protocol
import zipfile

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, mapping, shape
from shapely.validation import make_valid
from sqlalchemy import select

from app.db.fra_operational_models import SpatialImportBatch, SpatialReferenceFeature
from app.db.models import User
from app.services.audit import record_audit


DATASET_KINDS = {
    "administrative_boundary",
    "protected_area",
    "forest_compartment",
    "water_body",
    "cadastral_parcel",
}


class SpatialImportValidationError(ValueError):
    pass


@dataclass(frozen=True)
class VectorDataset:
    crs: str
    features: list[dict]
    normalized_crs: str = "EPSG:4326"


class VectorDatasetReader(Protocol):
    def read(self, content: bytes, filename: str) -> VectorDataset:
        ...


def _canonical_crs(value: str | None) -> str:
    normalized = str(value or "").strip().upper().replace(" ", "")
    aliases = {
        "EPSG:4326": "EPSG:4326",
        "URN:OGC:DEF:CRS:OGC:1.3:CRS84": "EPSG:4326",
        "CRS84": "EPSG:4326",
    }
    if normalized in aliases:
        return aliases[normalized]
    epsg = re.fullmatch(r"EPSG:(\d{3,6})", normalized)
    if epsg:
        return f"EPSG:{epsg.group(1)}"
    raise SpatialImportValidationError("CRS must use an explicit EPSG code.")


class GeoJSONDatasetReader:
    def read(self, content: bytes, filename: str) -> VectorDataset:
        if Path(filename).suffix.casefold() not in {".geojson", ".json"}:
            raise SpatialImportValidationError("The built-in reader accepts GeoJSON files only.")
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SpatialImportValidationError("The GeoJSON file is not valid UTF-8 JSON.") from error
        if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
            raise SpatialImportValidationError("A GeoJSON FeatureCollection is required.")
        features = payload.get("features")
        if not isinstance(features, list):
            raise SpatialImportValidationError("GeoJSON features must be an array.")
        crs_value = "EPSG:4326"
        crs = payload.get("crs")
        if crs is not None:
            if not isinstance(crs, dict) or not isinstance(crs.get("properties"), dict):
                raise SpatialImportValidationError("GeoJSON CRS metadata is invalid.")
            crs_value = str(crs["properties"].get("name") or "")
        return VectorDataset(crs=_canonical_crs(crs_value), features=features)


def _validate_vector_archive(content: bytes) -> None:
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(content)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > 200:
                raise SpatialImportValidationError("Shapefile archive entry count is invalid.")
            total_size = 0
            suffixes = set()
            for entry in entries:
                path = Path(entry.filename.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts or ":" in entry.filename:
                    raise SpatialImportValidationError("Shapefile archive contains an unsafe path.")
                total_size += entry.file_size
                suffixes.add(path.suffix.casefold())
            if total_size > 100 * 1024 * 1024:
                raise SpatialImportValidationError("Shapefile archive expands beyond 100 MB.")
            if not {".shp", ".shx", ".dbf"}.issubset(suffixes):
                raise SpatialImportValidationError("Shapefile archive is missing .shp, .shx, or .dbf components.")
    except zipfile.BadZipFile as error:
        raise SpatialImportValidationError("Shapefile upload is not a valid ZIP archive.") from error


class FionaDatasetReader:
    """Optional GDAL-backed reader for zipped Shapefile, KML, and GeoPackage."""

    def read(self, content: bytes, filename: str) -> VectorDataset:
        suffix = Path(filename).suffix.casefold()
        if suffix == ".zip":
            _validate_vector_archive(content)
        elif suffix not in {".gpkg", ".kml"}:
            raise SpatialImportValidationError("Unsupported GDAL vector dataset format.")
        try:
            import fiona
            from fiona.io import MemoryFile, ZipMemoryFile
            from fiona.transform import transform_geom
        except ImportError as error:
            raise SpatialImportValidationError(
                "Optional Fiona/GDAL vector support is not installed."
            ) from error
        try:
            memory_context = (
                ZipMemoryFile(content)
                if suffix == ".zip"
                else MemoryFile(content, filename=filename)
            )
            with memory_context as memory:
                layers = memory.listlayers()
                if not layers:
                    raise SpatialImportValidationError("Vector dataset has no readable layers.")
                with memory.open(layer=layers[0]) as source:
                    crs = source.crs
                    epsg = crs.to_epsg() if hasattr(crs, "to_epsg") else None
                    source_crs = f"EPSG:{epsg}" if epsg else str(source.crs_wkt or crs)
                    detected = _canonical_crs(source_crs)
                    features = []
                    for item in source:
                        geometry = transform_geom(detected, "EPSG:4326", item["geometry"])
                        features.append({
                            "type": "Feature",
                            "id": item.get("id"),
                            "properties": dict(item.get("properties") or {}),
                            "geometry": dict(geometry),
                        })
        except SpatialImportValidationError:
            raise
        except Exception as error:
            raise SpatialImportValidationError("Vector dataset could not be read safely.") from error
        return VectorDataset(crs=detected, features=features, normalized_crs="EPSG:4326")


def reader_for_filename(filename: str) -> VectorDatasetReader:
    return (
        GeoJSONDatasetReader()
        if Path(filename).suffix.casefold() in {".geojson", ".json"}
        else FionaDatasetReader()
    )


def _polygon_parts(geometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        return [part for item in geometry.geoms for part in _polygon_parts(item)]
    return []


def _normalized_geometry(value: dict) -> tuple[dict, bool]:
    try:
        geometry = shape(value)
    except (TypeError, ValueError, KeyError) as error:
        raise SpatialImportValidationError("Feature geometry is not valid GeoJSON.") from error
    if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        raise SpatialImportValidationError("Only Polygon and MultiPolygon features are supported.")
    repaired = not geometry.is_valid
    if repaired:
        geometry = make_valid(geometry)
    parts = [part for part in _polygon_parts(geometry) if not part.is_empty and part.is_valid]
    if not parts:
        raise SpatialImportValidationError("Feature geometry could not be repaired as a polygon.")
    normalized = MultiPolygon(parts)
    return mapping(normalized), repaired


def _source_record_id(feature: dict, geometry: dict, properties: dict) -> str:
    explicit = properties.get("source_record_id") or feature.get("id")
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()[:255]
    canonical = json.dumps(
        {"geometry": geometry, "properties": properties},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stage_spatial_import(
    session,
    *,
    content: bytes,
    filename: str,
    dataset_kind: str,
    source_authority: str,
    source_version: str,
    declared_crs: str,
    actor_id,
    idempotency_key: str,
    synthetic: bool,
    storage_key: str | None = None,
    reader: VectorDatasetReader | None = None,
    request_id: str | None = None,
) -> SpatialImportBatch:
    kind = dataset_kind.strip().casefold()
    authority = " ".join(source_authority.split())
    version = " ".join(source_version.split())
    key = " ".join(idempotency_key.split())
    if kind not in DATASET_KINDS:
        raise SpatialImportValidationError("Unsupported FRA reference dataset kind.")
    if not authority or not version or not key:
        raise SpatialImportValidationError("Source authority, source version, and idempotency key are required.")
    if session.get(User, actor_id) is None:
        raise SpatialImportValidationError("The spatial import actor does not exist.")
    existing_batch = session.scalar(
        select(SpatialImportBatch).where(
            SpatialImportBatch.created_by == actor_id,
            SpatialImportBatch.idempotency_key == key,
        )
    )
    if existing_batch is not None:
        return existing_batch

    declared = _canonical_crs(declared_crs)
    dataset = (reader or GeoJSONDatasetReader()).read(content, filename)
    detected = _canonical_crs(dataset.crs)
    if _canonical_crs(dataset.normalized_crs) != "EPSG:4326":
        raise SpatialImportValidationError("Vector geometries must be normalized to EPSG:4326.")
    if declared != detected:
        raise SpatialImportValidationError(
            f"Declared CRS {declared} does not match detected CRS {detected}."
        )
    classification = "synthetic" if synthetic else "declared_authoritative"
    batch = SpatialImportBatch(
        dataset_kind=kind,
        source_authority=authority,
        source_version=version,
        state="staged",
        original_filename=Path(filename).name[:255],
        storage_key=storage_key,
        declared_crs=declared,
        detected_crs=detected,
        record_count=len(dataset.features),
        provenance_json={
            "source": authority,
            "source_version": version,
            "classification": classification,
            "normalized_crs": "EPSG:4326",
            "reader": type(reader or GeoJSONDatasetReader()).__name__,
        },
        synthetic=synthetic,
        created_by=actor_id,
        idempotency_key=key,
    )
    session.add(batch)
    session.flush()
    seen = set()
    errors = []
    for index, raw_feature in enumerate(dataset.features, start=1):
        try:
            if not isinstance(raw_feature, dict) or raw_feature.get("type") != "Feature":
                raise SpatialImportValidationError("Entry is not a GeoJSON Feature.")
            properties = raw_feature.get("properties") or {}
            if not isinstance(properties, dict):
                raise SpatialImportValidationError("Feature properties must be an object.")
            geometry, repaired = _normalized_geometry(raw_feature.get("geometry"))
            source_record_id = _source_record_id(raw_feature, geometry, properties)
            duplicate = source_record_id in seen or session.scalar(
                select(SpatialReferenceFeature.id).where(
                    SpatialReferenceFeature.source_authority == authority,
                    SpatialReferenceFeature.source_version == version,
                    SpatialReferenceFeature.source_record_id == source_record_id,
                ).limit(1)
            )
            if duplicate:
                batch.duplicate_count += 1
                continue
            seen.add(source_record_id)
            checksum = hashlib.sha256(
                json.dumps(geometry, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            session.add(SpatialReferenceFeature(
                import_batch=batch,
                dataset_kind=kind,
                source_authority=authority,
                source_version=version,
                source_record_id=source_record_id,
                geometry=geometry,
                properties_json=dict(properties),
                provenance_json={
                    "source": authority,
                    "source_version": version,
                    "source_record_id": source_record_id,
                    "geometry_checksum": checksum,
                    "repaired": repaired,
                    "classification": classification,
                },
                published=False,
                synthetic=synthetic,
            ))
            batch.valid_count += 1
            if repaired:
                batch.repaired_count += 1
        except SpatialImportValidationError as error:
            batch.invalid_count += 1
            errors.append({"feature_index": index, "message": str(error)})
    batch.error_summary_json = {"features": errors}
    session.flush()
    record_audit(
        session,
        actor_id=actor_id,
        action="fra_spatial_import_staged",
        entity_type="spatial_import_batch",
        entity_id=batch.id,
        after={
            "dataset_kind": kind,
            "record_count": batch.record_count,
            "valid_count": batch.valid_count,
            "classification": classification,
        },
        request_id=request_id,
    )
    return batch


def publish_spatial_import(
    session,
    batch: SpatialImportBatch,
    *,
    reviewer_id,
    request_id: str | None = None,
) -> SpatialImportBatch:
    reviewer = session.get(User, reviewer_id)
    if reviewer is None or reviewer.role not in {"reviewer", "admin"}:
        raise PermissionError("Spatial reference publication requires a reviewer or admin.")
    if batch.state == "published":
        return batch
    if not batch.features:
        raise SpatialImportValidationError("A spatial import with no valid features cannot be published.")
    batch.state = "published"
    batch.reviewed_by = reviewer_id
    from datetime import datetime, timezone
    batch.reviewed_at = datetime.now(timezone.utc)
    batch.published_at = batch.reviewed_at
    classification = (
        "published_synthetic_reference" if batch.synthetic
        else "published_authoritative_reference"
    )
    batch.provenance_json = {**dict(batch.provenance_json), "classification": classification}
    for feature in batch.features:
        feature.published = True
        feature.provenance_json = {
            **dict(feature.provenance_json),
            "classification": classification,
        }
    session.flush()
    record_audit(
        session,
        actor_id=reviewer_id,
        action="fra_spatial_import_published",
        entity_type="spatial_import_batch",
        entity_id=batch.id,
        after={"classification": classification, "feature_count": len(batch.features)},
        request_id=request_id,
    )
    return batch


__all__ = [
    "GeoJSONDatasetReader",
    "FionaDatasetReader",
    "SpatialImportValidationError",
    "VectorDataset",
    "VectorDatasetReader",
    "publish_spatial_import",
    "reader_for_filename",
    "stage_spatial_import",
]
