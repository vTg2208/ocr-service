"""Queued, versioned historical evidence orchestration for FRA claims."""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import base64
import hashlib
import json
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from sqlalchemy import select

from app.db.fra_completion_models import ModelVersion
from app.db.fra_models import FRAClaim
from app.db.fra_operational_models import ImageryArtifact, ImagerySceneRecord
from app.db.models import User
from app.services.audit import record_audit
from app.services.model_gateway import ModelRegistrationError, validate_model_output
from app.services.processing_jobs import enqueue_job
from app.services.stac_imagery import STACProviderError


class HistoricalEvidenceError(RuntimeError):
    def __init__(self, message: str, *, retriable: bool):
        self.retriable = retriable
        super().__init__(message)


@dataclass(frozen=True)
class HistoricalProcessingResult:
    content: bytes
    statistics: dict
    quality_flags: list[str]
    processor_version: str
    model_version: str
    provenance: dict = field(default_factory=dict)


def _default_rest_transport(endpoint: str, payload: dict, timeout: float) -> dict:
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - allow-listed below.
        return json.loads(response.read().decode("utf-8"))


class RESTHistoricalProcessor:
    """Strict attachment boundary for a separately deployed historical model."""

    def __init__(self, model: ModelVersion, transport):
        self.version = model.version
        self.model_id = str(model.id)
        configuration = dict(model.configuration_json or {})
        endpoint = str(configuration.get("endpoint") or "").strip()
        parsed = urlparse(endpoint)
        allowed_hosts = {
            str(host).strip().casefold()
            for host in configuration.get("allowed_hosts", [])
            if str(host).strip()
        }
        local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
        if parsed.scheme != "https" and not local_http:
            raise ModelRegistrationError("Historical model endpoint must use HTTPS.")
        if not parsed.hostname or parsed.hostname.casefold() not in allowed_hosts:
            raise ModelRegistrationError("Historical model host is not allow-listed.")
        self.endpoint = endpoint
        self.endpoint_host = parsed.hostname
        self.timeout = float(configuration.get("timeout_seconds", 60))
        if not 0 < self.timeout <= 120:
            raise ModelRegistrationError("Historical model timeout must be between 0 and 120 seconds.")
        self._transport = transport

    def process(self, scene, geometry: dict, target_year: int) -> HistoricalProcessingResult:
        response = self._transport(
            self.endpoint,
            {
                "scene": {
                    "id": scene.scene_id,
                    "provider": scene.provider,
                    "collection": scene.collection,
                    "acquired_at": scene.acquired_at.isoformat(),
                    "assets": scene.private_asset_references,
                },
                "geometry": geometry,
                "target_year": target_year,
            },
            self.timeout,
        )
        if not isinstance(response, dict):
            raise HistoricalEvidenceError("Historical model response must be an object.", retriable=False)
        if response.get("processor_version") != self.version or response.get("model_version") != self.version:
            raise HistoricalEvidenceError("Historical model response version mismatch.", retriable=False)
        statistics = response.get("statistics")
        provenance = response.get("provenance") or {}
        flags = response.get("quality_flags") or []
        if not isinstance(statistics, dict) or not isinstance(provenance, dict):
            raise HistoricalEvidenceError("Historical model statistics and provenance must be objects.", retriable=False)
        if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
            raise HistoricalEvidenceError("Historical model quality flags must be strings.", retriable=False)
        validate_model_output(statistics)
        validate_model_output(provenance)
        encoded = response.get("artifact_base64")
        if not isinstance(encoded, str) or len(encoded) > 35_000_000:
            raise HistoricalEvidenceError("Historical model artifact is missing or too large.", retriable=False)
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as error:
            raise HistoricalEvidenceError("Historical model artifact encoding is invalid.", retriable=False) from error
        if not content or len(content) > 25_000_000:
            raise HistoricalEvidenceError("Historical model artifact is empty or too large.", retriable=False)
        return HistoricalProcessingResult(
            content=content,
            statistics=statistics,
            quality_flags=flags,
            processor_version=self.version,
            model_version=self.version,
            provenance={
                **provenance,
                "adapter": "rest",
                "endpoint_host": self.endpoint_host,
                "model_id": self.model_id,
            },
        )


def create_historical_processor(model: ModelVersion, *, rest_transport=None):
    if model.task != "historical_evidence":
        raise ModelRegistrationError("Model task is not historical FRA evidence.")
    configuration = dict(model.configuration_json or {})
    if model.status != "active" or configuration.get("ready") is not True:
        raise ModelRegistrationError("Historical model is not ready and active.")
    if str(model.adapter_type or "").strip().casefold() != "rest":
        raise ModelRegistrationError("Historical evidence currently supports an attached REST model.")
    return RESTHistoricalProcessor(model, rest_transport or _default_rest_transport)


def _current_geometry(claim: FRAClaim):
    return max(claim.geometry_versions, key=lambda item: item.version) if claim.geometry_versions else None


def request_historical_evidence(
    session,
    claim: FRAClaim,
    *,
    target_years: list[int],
    actor_id,
    idempotency_key: str,
    request_id: str | None = None,
):
    if session.get(User, actor_id) is None:
        raise ValueError("The historical evidence actor does not exist.")
    geometry = _current_geometry(claim)
    if geometry is None:
        raise ValueError("The claim requires a geometry version before historical processing.")
    years = sorted(set(target_years))
    current_year = datetime.now(timezone.utc).year
    if not years or len(years) > 10 or any(year < 1972 or year > current_year for year in years):
        raise ValueError("Provide between one and ten target years from 1972 to the current year.")
    key = idempotency_key.strip()
    if not key:
        raise ValueError("An idempotency key is required.")
    job = enqueue_job(
        session,
        task_type="historical_evidence",
        entity_type="fra_claim",
        entity_id=claim.id,
        actor_id=actor_id,
        idempotency_key=key,
        payload={
            "claim_id": str(claim.id),
            "geometry_version_id": str(geometry.id),
            "target_years": years,
        },
        max_attempts=3,
    )
    record_audit(
        session,
        actor_id=actor_id,
        action="fra_historical_evidence_requested",
        entity_type="fra_claim",
        entity_id=claim.id,
        after={"processing_job_id": str(job.id), "target_years": years},
        request_id=request_id,
    )
    return job


def _scene_record(session, candidate) -> ImagerySceneRecord:
    existing = session.scalar(select(ImagerySceneRecord).where(
        ImagerySceneRecord.provider == candidate.provider,
        ImagerySceneRecord.collection == candidate.collection,
        ImagerySceneRecord.scene_id == candidate.scene_id,
    ))
    if existing is not None:
        return existing
    scene = ImagerySceneRecord(
        provider=candidate.provider,
        collection=candidate.collection,
        scene_id=candidate.scene_id,
        acquired_at=candidate.acquired_at,
        footprint=candidate.footprint,
        cloud_cover=candidate.cloud_cover,
        asset_references_json=dict(candidate.private_asset_references),
        license_reference=candidate.license_reference,
        status="selected",
        provenance_json={"source": "stac", "asset_keys": list(candidate.asset_keys)},
        synthetic=False,
    )
    session.add(scene); session.flush(); return scene


def process_historical_evidence_job(
    session,
    job,
    *,
    stac_client,
    processor,
    storage,
    model: ModelVersion | None,
) -> dict:
    if processor is None or model is None:
        return {"status": "insufficient_model", "artifact_ids": []}
    if processor.version != model.version:
        raise HistoricalEvidenceError("Historical processor version does not match the registered model.", retriable=False)
    payload = dict(job.payload_json or {})
    claim = session.get(FRAClaim, job.entity_id)
    if claim is None:
        raise HistoricalEvidenceError("Historical evidence claim no longer exists.", retriable=False)
    geometry_version = _current_geometry(claim)
    if geometry_version is None or str(geometry_version.id) != payload.get("geometry_version_id"):
        raise HistoricalEvidenceError("Claim geometry changed; request historical evidence again.", retriable=False)
    stored_keys = []
    artifact_ids = []
    missing_years = []
    nested = session.begin_nested()
    try:
        for target_year in payload.get("target_years", []):
            artifact_type = f"historical_land_observation:{target_year}"
            existing = session.scalar(select(ImageryArtifact).where(
                ImageryArtifact.claim_id == claim.id,
                ImageryArtifact.geometry_version_id == geometry_version.id,
                ImageryArtifact.artifact_type == artifact_type,
                ImageryArtifact.processor_version == model.version,
            ))
            if existing is not None:
                artifact_ids.append(str(existing.id)); continue
            try:
                scenes = stac_client.search(
                    geometry_version.geometry,
                    (date(target_year, 1, 1), date(target_year, 12, 31)),
                    ["landsat-c2-l2"],
                    40,
                )
            except STACProviderError as error:
                raise HistoricalEvidenceError(str(error), retriable=True) from error
            if not scenes:
                missing_years.append(target_year); continue
            candidate = scenes[0]
            result = processor.process(candidate, geometry_version.geometry, target_year)
            if result.processor_version != model.version:
                raise HistoricalEvidenceError("Historical processor result version mismatch.", retriable=False)
            if not isinstance(result.content, bytes) or not result.content:
                raise HistoricalEvidenceError("Historical processor returned no artifact content.", retriable=False)
            validate_model_output(result.statistics)
            storage_key = storage.put(result.content, ".json")
            stored_keys.append(storage_key)
            scene = _scene_record(session, candidate)
            artifact = ImageryArtifact(
                claim_id=claim.id,
                geometry_version_id=geometry_version.id,
                imagery_scene_id=scene.id,
                processing_job_id=job.id,
                artifact_type=artifact_type,
                target_year=target_year,
                storage_key=storage_key,
                content_sha256=hashlib.sha256(result.content).hexdigest(),
                processor_version=result.processor_version,
                model_version_id=model.id,
                parameters_json={"target_year": target_year, "max_cloud": 40},
                statistics_json=dict(result.statistics),
                quality_flags_json=list(result.quality_flags),
                provenance_json={
                    **dict(result.provenance),
                    "provider": candidate.provider,
                    "collection": candidate.collection,
                    "scene_id": candidate.scene_id,
                    "acquired_at": candidate.acquired_at.isoformat(),
                    "legal_role": "supporting_observation",
                },
                state="completed",
                verification_state="unverified",
                synthetic=False,
            )
            session.add(artifact); session.flush(); artifact_ids.append(str(artifact.id))
        nested.commit()
    except Exception as error:
        nested.rollback()
        for key in stored_keys:
            storage.delete(key)
        if isinstance(error, HistoricalEvidenceError):
            raise
        raise HistoricalEvidenceError(str(error), retriable=True) from error
    status = "completed" if artifact_ids and not missing_years else "partial" if artifact_ids else "insufficient_imagery"
    record_audit(
        session,
        actor_id=job.requested_by,
        action="fra_historical_evidence_processed",
        entity_type="fra_claim",
        entity_id=claim.id,
        after={"status": status, "artifact_count": len(artifact_ids), "missing_years": missing_years},
    )
    return {"status": status, "artifact_ids": artifact_ids, "missing_years": missing_years}


__all__ = [
    "HistoricalEvidenceError", "HistoricalProcessingResult", "RESTHistoricalProcessor",
    "create_historical_processor", "process_historical_evidence_job",
    "request_historical_evidence",
]
