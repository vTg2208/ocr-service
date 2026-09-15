"""Real STAC-to-analysis-ready raster ingestion for spatial FRA records."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import re
from urllib.parse import urlparse

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.mask import mask
from rasterio.warp import Resampling, reproject, transform_geom
from sqlalchemy import select

from app.db.fra_models import FRAClaim
from app.db.fra_operational_models import ImageryArtifact, ImagerySceneRecord
from app.db.models import User
from app.services.audit import record_audit
from app.services.processing_jobs import enqueue_job
from app.services.stac_imagery import STACProviderError


class SatelliteIngestionError(RuntimeError):
    def __init__(self, message: str, *, retriable: bool = False):
        self.retriable = retriable
        super().__init__(message)


@dataclass(frozen=True)
class PreparedRaster:
    content: bytes
    width: int
    height: int
    crs: str
    transform: list[float]
    band_keys: list[str]
    valid_pixel_percent: float
    processor_version: str


@contextmanager
def _default_opener(href: str):
    try:
        with rasterio.Env(
            GDAL_HTTP_MAX_RETRY="2",
            GDAL_HTTP_RETRY_DELAY="1",
            GDAL_HTTP_CONNECTTIMEOUT="15",
            GDAL_HTTP_TIMEOUT="60",
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
            CPL_VSIL_CURL_USE_HEAD="NO",
        ):
            with rasterio.open(href) as dataset:
                yield dataset
    except SatelliteIngestionError:
        raise
    except Exception as error:
        raise SatelliteIngestionError(
            "The selected satellite raster could not be read.", retriable=True
        ) from error


class COGRasterPreprocessor:
    """Read only the FRA window from allow-listed Cloud-Optimized GeoTIFF assets."""

    version = "fra-cog-stack-v1"

    def __init__(self, *, allowed_hosts: set[str], opener=None, max_output_pixels: int = 4_000_000):
        self.allowed_hosts = {str(host).strip().casefold() for host in allowed_hosts if str(host).strip()}
        self.opener = opener or _default_opener
        self.max_output_pixels = int(max_output_pixels)
        if not self.allowed_hosts:
            raise SatelliteIngestionError("At least one satellite raster host must be allow-listed.")
        if not 1 <= self.max_output_pixels <= 25_000_000:
            raise SatelliteIngestionError("Satellite raster pixel limit is invalid.")

    def _href(self, asset: dict, band_key: str) -> str:
        href = str(asset.get("href") or "").strip() if isinstance(asset, dict) else ""
        parsed = urlparse(href)
        local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
        if (
            not href or not parsed.hostname
            or (parsed.scheme != "https" and not local_http)
            or parsed.hostname.casefold() not in self.allowed_hosts
        ):
            raise SatelliteIngestionError(
                f"Satellite asset host for band {band_key!r} must be allow-listed and use HTTPS."
            )
        return href

    @staticmethod
    def _read_window(dataset, geometry: dict):
        if dataset.crs is None:
            raise SatelliteIngestionError("Satellite raster CRS is missing.")
        try:
            projected = transform_geom("EPSG:4326", dataset.crs, geometry, precision=12)
            values, output_transform = mask(
                dataset, [projected], crop=True, indexes=1, filled=False
            )
        except ValueError as error:
            raise SatelliteIngestionError(
                "Satellite raster does not overlap the current FRA geometry."
            ) from error
        array = np.ma.asarray(values)
        if array.ndim == 3:
            array = array[0]
        return array.astype("float32").filled(np.nan), output_transform

    def prepare(self, assets: dict, band_keys: list[str], geometry: dict) -> PreparedRaster:
        if not band_keys or len(band_keys) > 6 or len(set(band_keys)) != len(band_keys):
            raise SatelliteIngestionError("Choose between one and six distinct satellite bands.")
        reference = None
        arrays: list[np.ndarray] = []
        for band_key in band_keys:
            if band_key not in assets:
                raise SatelliteIngestionError(f"Selected scene has no {band_key!r} raster band.")
            href = self._href(assets[band_key], band_key)
            with self.opener(href) as dataset:
                array, output_transform = self._read_window(dataset, geometry)
                if reference is None:
                    if array.size > self.max_output_pixels:
                        raise SatelliteIngestionError("Prepared satellite raster exceeds the pixel limit.")
                    reference = {
                        "height": array.shape[0], "width": array.shape[1],
                        "transform": output_transform, "crs": dataset.crs,
                    }
                elif (
                    array.shape != (reference["height"], reference["width"])
                    or output_transform != reference["transform"] or dataset.crs != reference["crs"]
                ):
                    aligned = np.full(
                        (reference["height"], reference["width"]), np.nan, dtype="float32"
                    )
                    reproject(
                        source=array, destination=aligned,
                        src_transform=output_transform, src_crs=dataset.crs, src_nodata=np.nan,
                        dst_transform=reference["transform"], dst_crs=reference["crs"],
                        dst_nodata=np.nan, resampling=Resampling.bilinear,
                    )
                    array = aligned
                arrays.append(array)
        if reference is None:
            raise SatelliteIngestionError("No satellite raster bands were prepared.")
        stack = np.stack(arrays).astype("float32")
        valid_percent = round(float(np.isfinite(stack).all(axis=0).mean() * 100), 4)
        if valid_percent <= 0:
            raise SatelliteIngestionError("Prepared satellite raster contains no valid FRA pixels.")
        with MemoryFile() as memory:
            with memory.open(
                driver="GTiff", width=reference["width"], height=reference["height"],
                count=len(band_keys), dtype="float32", crs=reference["crs"],
                transform=reference["transform"], nodata=np.nan, compress="deflate",
            ) as output:
                output.write(stack)
                output.descriptions = tuple(band_keys)
                output.update_tags(
                    pipeline="AranyaSetu FRA satellite ingestion",
                    processor_version=self.version,
                )
            content = memory.read()
        return PreparedRaster(
            content=content, width=reference["width"], height=reference["height"],
            crs=str(reference["crs"]), transform=list(reference["transform"])[:6],
            band_keys=list(band_keys), valid_pixel_percent=valid_percent,
            processor_version=self.version,
        )


def _current_geometry(claim: FRAClaim):
    return max(claim.geometry_versions, key=lambda item: item.version) if claim.geometry_versions else None


def request_satellite_ingestion(
    session, claim: FRAClaim, *, start_date: date, end_date: date,
    collection: str, band_keys: list[str], max_cloud: float, actor_id,
    idempotency_key: str, request_id: str | None = None,
):
    if session.get(User, actor_id) is None:
        raise ValueError("The satellite ingestion actor does not exist.")
    geometry = _current_geometry(claim)
    if geometry is None:
        raise ValueError("The claim requires a geometry version before satellite ingestion.")
    if start_date > end_date or end_date > datetime.now(timezone.utc).date():
        raise ValueError("Satellite ingestion date range is invalid or extends into the future.")
    if (end_date - start_date).days > 366:
        raise ValueError("Satellite ingestion requests are limited to a 366-day range.")
    normalized_collection = collection.strip()
    normalized_bands = [str(key).strip() for key in band_keys]
    if not normalized_collection:
        raise ValueError("A satellite collection is required.")
    if (
        not normalized_bands or len(normalized_bands) > 6
        or len(set(normalized_bands)) != len(normalized_bands)
        or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", key) for key in normalized_bands)
    ):
        raise ValueError("Choose between one and six distinct valid satellite band keys.")
    if not 0 <= float(max_cloud) <= 100:
        raise ValueError("Maximum cloud cover must be between 0 and 100.")
    job = enqueue_job(
        session, task_type="satellite_ingestion", entity_type="fra_claim",
        entity_id=claim.id, actor_id=actor_id, idempotency_key=idempotency_key,
        payload={
            "claim_id": str(claim.id), "geometry_version_id": str(geometry.id),
            "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
            "collection": normalized_collection, "band_keys": normalized_bands,
            "max_cloud": float(max_cloud),
        },
    )
    record_audit(
        session, actor_id=actor_id, action="fra_satellite_ingestion_requested",
        entity_type="fra_claim", entity_id=claim.id,
        after={
            "processing_job_id": str(job.id), "collection": normalized_collection,
            "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
            "band_keys": normalized_bands,
        }, request_id=request_id,
    )
    return job


def _scene_record(session, candidate) -> ImagerySceneRecord:
    scene = session.scalar(select(ImagerySceneRecord).where(
        ImagerySceneRecord.provider == candidate.provider,
        ImagerySceneRecord.collection == candidate.collection,
        ImagerySceneRecord.scene_id == candidate.scene_id,
    ))
    if scene is None:
        scene = ImagerySceneRecord(
            provider=candidate.provider, collection=candidate.collection,
            scene_id=candidate.scene_id, acquired_at=candidate.acquired_at,
            footprint=candidate.footprint, cloud_cover=candidate.cloud_cover,
            asset_references_json=dict(candidate.private_asset_references),
            license_reference=candidate.license_reference, status="ingested",
            provenance_json={"source": "stac", "asset_keys": list(candidate.asset_keys)},
            synthetic=False,
        )
        session.add(scene)
    else:
        scene.status = "ingested"
    session.flush()
    return scene


def process_satellite_ingestion_job(session, job, *, stac_client, preprocessor, storage) -> dict:
    if job.task_type != "satellite_ingestion":
        raise SatelliteIngestionError("The job is not a satellite-ingestion task.")
    payload = dict(job.payload_json or {})
    claim = session.get(FRAClaim, job.entity_id)
    if claim is None:
        raise SatelliteIngestionError("Satellite-ingestion claim no longer exists.")
    geometry = _current_geometry(claim)
    if geometry is None or str(geometry.id) != payload.get("geometry_version_id"):
        raise SatelliteIngestionError("Claim geometry changed; request satellite ingestion again.")
    artifact_type = f"analysis_ready_raster:{payload['start_date']}:{payload['end_date']}"
    existing = session.scalar(select(ImageryArtifact).where(
        ImageryArtifact.claim_id == claim.id,
        ImageryArtifact.geometry_version_id == geometry.id,
        ImageryArtifact.artifact_type == artifact_type,
        ImageryArtifact.processor_version == preprocessor.version,
    ))
    if existing is not None:
        return {"status": "completed", "artifact_id": str(existing.id), "replayed": True}
    try:
        scenes = stac_client.search(
            geometry.geometry,
            (date.fromisoformat(payload["start_date"]), date.fromisoformat(payload["end_date"])),
            [payload["collection"]], payload["max_cloud"],
        )
    except STACProviderError as error:
        raise SatelliteIngestionError(str(error), retriable=True) from error
    required = payload["band_keys"]
    candidate = next((scene for scene in scenes if set(required).issubset(scene.asset_keys)), None)
    if candidate is None:
        return {"status": "insufficient_imagery", "artifact_id": None, "replayed": False}
    prepared = preprocessor.prepare(
        candidate.private_asset_references, required, geometry.geometry
    )
    storage_key = storage.put(prepared.content, ".tif")
    try:
        scene = _scene_record(session, candidate)
        artifact = ImageryArtifact(
            claim_id=claim.id, geometry_version_id=geometry.id,
            imagery_scene_id=scene.id, processing_job_id=job.id,
            artifact_type=artifact_type, target_year=candidate.acquired_at.year,
            storage_key=storage_key, content_sha256=hashlib.sha256(prepared.content).hexdigest(),
            processor_version=prepared.processor_version, model_version_id=None,
            parameters_json={
                "collection": payload["collection"], "band_keys": prepared.band_keys,
                "start_date": payload["start_date"], "end_date": payload["end_date"],
                "max_cloud": payload["max_cloud"],
            },
            statistics_json={
                "width": prepared.width, "height": prepared.height,
                "crs": prepared.crs, "transform": prepared.transform,
                "band_keys": prepared.band_keys,
                "valid_pixel_percent": prepared.valid_pixel_percent,
            },
            quality_flags_json=(
                ["cloud_cover_unavailable"] if candidate.cloud_cover is None else []
            ),
            provenance_json={
                "source": "stac_cog_window", "provider": candidate.provider,
                "collection": candidate.collection, "scene_id": candidate.scene_id,
                "acquired_at": candidate.acquired_at.isoformat(),
                "processor_version": prepared.processor_version,
                "legal_role": "supporting_observation",
            },
            state="completed", verification_state="unverified", synthetic=False,
        )
        session.add(artifact); session.flush()
        record_audit(
            session, actor_id=job.requested_by, action="fra_satellite_ingestion_completed",
            entity_type="fra_claim", entity_id=claim.id,
            after={
                "artifact_id": str(artifact.id), "scene_id": candidate.scene_id,
                "collection": candidate.collection, "band_keys": prepared.band_keys,
                "valid_pixel_percent": prepared.valid_pixel_percent,
            },
        )
    except Exception:
        storage.delete(storage_key)
        raise
    return {"status": "completed", "artifact_id": str(artifact.id), "replayed": False}


__all__ = [
    "COGRasterPreprocessor", "PreparedRaster", "SatelliteIngestionError",
    "process_satellite_ingestion_job", "request_satellite_ingestion",
]
