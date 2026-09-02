"""Pluggable, non-adjudicative satellite observation foundation."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol
import time

from sqlalchemy import select

from app.db.fra_models import FRAEvidenceItem, SatelliteObservation
from app.services.audit import record_audit


ASSET_CLASSES = {
    "agricultural_cover",
    "anganwadi",
    "barren_land",
    "borewell",
    "bridge",
    "check_dam",
    "community_centre",
    "electricity_grid",
    "fisheries",
    "forest_cover",
    "forest_nursery",
    "grazing_land",
    "health_centre",
    "homestead",
    "irrigation_canal",
    "livestock",
    "market",
    "minor_forest_produce",
    "open_well",
    "pipeline",
    "plantation_orchard",
    "pond",
    "rainwater_harvesting",
    "river_stream",
    "road",
    "sanitation_toilet",
    "school",
    "scrubland",
    "solar_power",
    "storage_warehouse",
    "tap_water",
    "water_body",
    "water_tank",
}
BANNED_CONCLUSION_KEYS = {"valid", "invalid", "approved", "rejected", "eligibility"}


class SatelliteProviderUnavailable(RuntimeError):
    pass


class SatelliteEvidenceValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ImageryRequest:
    scene_id: str
    geometry: dict


@dataclass(frozen=True)
class ImageryScene:
    scene_id: str
    provider: str
    source_uri: str
    acquired_at: date
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AssetObservation:
    asset_class: str
    value: Any
    confidence: float


@dataclass(frozen=True)
class AnalysisResult:
    observations: list[AssetObservation]
    analyser_version: str
    processing_time_ms: int
    provenance: dict = field(default_factory=dict)


class ImageryProvider(Protocol):
    def acquire(self, request: ImageryRequest) -> ImageryScene:
        ...


class AssetAnalyser(Protocol):
    version: str

    def analyse(self, scene: ImageryScene, geometry: dict) -> AnalysisResult:
        ...


class LocalManifestImageryProvider:
    """Return only explicitly registered local/synthetic scene manifests."""

    def __init__(self, scenes: dict[str, ImageryScene]):
        self.scenes = dict(scenes)

    def acquire(self, request: ImageryRequest) -> ImageryScene:
        scene = self.scenes.get(request.scene_id)
        if scene is None:
            raise SatelliteProviderUnavailable(
                f"Satellite scene {request.scene_id!r} is unavailable from the local manifest."
            )
        return scene


class LocalObservationAnalyser:
    """Validate deterministic manifest observations; it does not analyse pixels."""

    def __init__(self, version: str):
        normalized = version.strip()
        if not normalized:
            raise SatelliteEvidenceValidationError("An analyser version is required.")
        self.version = normalized

    def analyse(self, scene: ImageryScene, geometry: dict) -> AnalysisResult:
        started = time.perf_counter()
        if not isinstance(geometry, dict) or geometry.get("type") != "MultiPolygon":
            raise SatelliteEvidenceValidationError(
                "Satellite observations require a GeoJSON MultiPolygon."
            )
        source = scene.metadata.get("observations")
        if not isinstance(source, list) or not source:
            raise SatelliteEvidenceValidationError(
                "The local scene manifest must contain at least one observation."
            )
        observations: list[AssetObservation] = []
        seen_classes: set[str] = set()
        for item in source:
            if not isinstance(item, dict):
                raise SatelliteEvidenceValidationError("Each observation must be an object.")
            banned = BANNED_CONCLUSION_KEYS.intersection(
                str(key).casefold() for key in item
            )
            if banned:
                raise SatelliteEvidenceValidationError(
                    "Satellite observations cannot contain an automated legal conclusion."
                )
            asset_class = str(item.get("asset_class") or "").strip()
            if asset_class not in ASSET_CLASSES:
                raise SatelliteEvidenceValidationError(
                    f"Unsupported satellite asset class: {asset_class or 'missing'}."
                )
            if asset_class in seen_classes:
                raise SatelliteEvidenceValidationError(
                    f"Scene contains duplicate observations for {asset_class}."
                )
            seen_classes.add(asset_class)
            confidence = item.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                raise SatelliteEvidenceValidationError("Observation confidence must be numeric.")
            if not 0 <= float(confidence) <= 1:
                raise SatelliteEvidenceValidationError("Observation confidence must be between 0 and 1.")
            value = item.get("value")
            if not isinstance(value, (str, int, float, bool)):
                raise SatelliteEvidenceValidationError(
                    "Observation value must be a string, number, or boolean."
                )
            observations.append(
                AssetObservation(asset_class, value, float(confidence))
            )
        return AnalysisResult(
            observations=observations,
            analyser_version=self.version,
            processing_time_ms=round((time.perf_counter() - started) * 1000),
            provenance={"mode": "local_manifest", "pixel_inference": False},
        )


def _current_geometry_version_id(claim):
    if not claim.geometry_versions:
        return None
    return max(claim.geometry_versions, key=lambda item: item.version).id


def create_supporting_observations(
    session,
    claim,
    *,
    scene: ImageryScene,
    geometry: dict,
    analyser: AssetAnalyser,
    actor_id,
    request_id: str | None,
) -> list[SatelliteObservation]:
    if not scene.scene_id.strip() or not scene.provider.strip() or not scene.source_uri.strip():
        raise SatelliteEvidenceValidationError(
            "Scene ID, provider, and private source URI are required."
        )
    result = analyser.analyse(scene, geometry)
    geometry_version_id = _current_geometry_version_id(claim)
    stored: list[SatelliteObservation] = []
    for item in result.observations:
        existing = session.scalar(
            select(SatelliteObservation).where(
                SatelliteObservation.provider == scene.provider,
                SatelliteObservation.scene_id == scene.scene_id,
                SatelliteObservation.claim_id == claim.id,
                SatelliteObservation.asset_class == item.asset_class,
            )
        )
        if existing is not None:
            stored.append(existing)
            continue
        observation = SatelliteObservation(
            claim=claim,
            geometry_version_id=geometry_version_id,
            scene_id=scene.scene_id,
            provider=scene.provider,
            source_uri=scene.source_uri,
            asset_class=item.asset_class,
            observed_value_json={"value": item.value},
            confidence=Decimal(str(item.confidence)),
            analyser_version=result.analyser_version,
            acquired_at=scene.acquired_at,
            processing_time_ms=result.processing_time_ms,
            provenance_json={
                **result.provenance,
                "scene_metadata": {
                    key: value for key, value in scene.metadata.items() if key != "observations"
                },
            },
        )
        session.add(observation)
        session.flush()
        evidence = FRAEvidenceItem(
            claim=claim,
            category="satellite_observation",
            legal_role="supporting",
            source=scene.provider,
            description=(
                f"Satellite observation for {item.asset_class} reported {item.value}. "
                "This is supporting evidence only and requires human verification."
            ),
            satellite_observation=observation,
            provenance_json={
                "scene_id": scene.scene_id,
                "analyser_version": result.analyser_version,
                "confidence": item.confidence,
            },
            captured_at=scene.acquired_at,
            verification_state="unverified",
            source_verified=False,
            created_by=actor_id,
        )
        session.add(evidence)
        record_audit(
            session,
            actor_id=actor_id,
            action="satellite_supporting_evidence_created",
            entity_type="fra_claim",
            entity_id=claim.id,
            after={
                "observation_id": str(observation.id),
                "asset_class": item.asset_class,
                "legal_role": "supporting",
                "source_verified": False,
            },
            request_id=request_id,
        )
        stored.append(observation)
    session.flush()
    return stored


def acquire_and_analyse(
    session,
    claim,
    *,
    request: ImageryRequest,
    provider: ImageryProvider,
    analyser: AssetAnalyser,
    actor_id,
    request_id: str | None,
) -> list[SatelliteObservation]:
    scene = provider.acquire(request)
    return create_supporting_observations(
        session,
        claim,
        scene=scene,
        geometry=request.geometry,
        analyser=analyser,
        actor_id=actor_id,
        request_id=request_id,
    )
