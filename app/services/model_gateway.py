"""Typed, replaceable model contracts with non-adjudicative output validation."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select

from app.db.fra_completion_models import ModelVersion
from app.services.audit import record_audit
from app.services.satellite_evidence import ASSET_CLASSES, ImageryProvider
from app.services.state_profiles import get_state_profile


BANNED_CONCLUSION_KEYS = {
    "valid",
    "invalid",
    "approved",
    "rejected",
    "eligibility",
    "sanctioned",
}


class ModelOutputValidationError(ValueError):
    pass


class ModelRegistrationError(ValueError):
    pass


@dataclass(frozen=True)
class OCRModelResult:
    raw_text: str
    confidence: float | None
    model_version: str
    processing_time_ms: int
    provenance: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EntityExtractionResult:
    fields: dict
    field_evidence: dict
    confidence: float | None
    model_version: str
    processing_time_ms: int
    provenance: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AssetDetectionResult:
    features: list[dict]
    confidence: float | None
    model_version: str
    processing_time_ms: int
    provenance: dict = field(default_factory=dict)


class DocumentOCRProvider(Protocol):
    version: str

    def recognize(self, document_reference: str, context: dict) -> OCRModelResult:
        ...


class FRAEntityExtractor(Protocol):
    version: str

    def extract(self, document_reference: str, manifest: dict) -> EntityExtractionResult:
        ...


class LandCoverClassifier(Protocol):
    version: str

    def classify(self, scene_reference: str, geometry: dict, context: dict) -> AssetDetectionResult:
        ...


class AssetDetector(Protocol):
    version: str

    def detect(self, scene_reference: str, geometry: dict, context: dict) -> AssetDetectionResult:
        ...


def _validate_confidence(value: Any, *, field_name: str = "confidence") -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelOutputValidationError(f"{field_name} must be numeric.")
    normalized = float(value)
    if not 0 <= normalized <= 1:
        raise ModelOutputValidationError(f"{field_name} must be between 0 and 1.")
    return normalized


def validate_model_output(output: Any) -> Any:
    """Reject decision-like output anywhere in a model response."""

    if isinstance(output, dict):
        banned = BANNED_CONCLUSION_KEYS.intersection(str(key).casefold() for key in output)
        if banned:
            raise ModelOutputValidationError(
                "Model output cannot contain an automated legal conclusion."
            )
        for value in output.values():
            validate_model_output(value)
    elif isinstance(output, list):
        for value in output:
            validate_model_output(value)
    return output


class ManifestFRAEntityExtractor:
    """Deterministic adapter for explicitly synthetic Tamil Nadu manifests."""

    def __init__(self, version: str):
        self.version = version.strip()
        if not self.version:
            raise ModelOutputValidationError("A model version is required.")

    def extract(self, document_reference: str, manifest: dict) -> EntityExtractionResult:
        if not document_reference.strip():
            raise ModelOutputValidationError("A document reference is required.")
        validate_model_output(manifest)
        profile = get_state_profile(manifest.get("state_code") or manifest.get("state") or "TN")
        fields = dict(manifest)
        fields["state"] = profile.name
        fields["state_code"] = profile.code
        for key, normalizer in (
            ("district", profile.normalize_district),
            ("block", profile.normalize_block),
            ("village", profile.normalize_village),
        ):
            if fields.get(key):
                fields[key] = normalizer(str(fields[key]))
        confidence = _validate_confidence(fields.pop("confidence", None))
        evidence = fields.pop("field_evidence", {})
        if not isinstance(evidence, dict):
            raise ModelOutputValidationError("field_evidence must be an object.")
        return EntityExtractionResult(
            fields=fields,
            field_evidence=evidence,
            confidence=confidence,
            model_version=self.version,
            processing_time_ms=0,
            provenance={
                "adapter": "manifest",
                "synthetic": True,
                "document_reference": document_reference,
            },
        )


class ManifestAssetDetector:
    """Replay declared synthetic observations without claiming pixel inference."""

    def __init__(self, version: str):
        self.version = version.strip()
        if not self.version:
            raise ModelOutputValidationError("A model version is required.")

    def detect(self, scene_reference: str, geometry: dict, context: dict) -> AssetDetectionResult:
        if not scene_reference.strip():
            raise ModelOutputValidationError("A scene reference is required.")
        if not isinstance(geometry, dict) or geometry.get("type") != "MultiPolygon":
            raise ModelOutputValidationError("Asset detection requires a GeoJSON MultiPolygon.")
        validate_model_output(context)
        if context.get("synthetic") is not True:
            raise ModelOutputValidationError("Manifest detections must be labelled synthetic.")
        source = context.get("features")
        if not isinstance(source, list) or not source:
            raise ModelOutputValidationError("Manifest detections require at least one feature.")
        features: list[dict] = []
        confidences: list[float] = []
        for item in source:
            if not isinstance(item, dict):
                raise ModelOutputValidationError("Each asset feature must be an object.")
            asset_class = str(item.get("asset_class") or "").strip()
            if asset_class not in ASSET_CLASSES:
                raise ModelOutputValidationError(f"Unsupported asset class: {asset_class or 'missing'}.")
            feature_geometry = item.get("geometry")
            if not isinstance(feature_geometry, dict) or feature_geometry.get("type") not in {
                "Point",
                "MultiPolygon",
            }:
                raise ModelOutputValidationError(
                    "Asset geometry must be a GeoJSON Point or MultiPolygon."
                )
            confidence = _validate_confidence(item.get("confidence"))
            if confidence is None:
                raise ModelOutputValidationError("Asset confidence is required.")
            confidences.append(confidence)
            features.append(
                {
                    "asset_class": asset_class,
                    "geometry": feature_geometry,
                    "value": item.get("value", {}),
                    "confidence": confidence,
                }
            )
        return AssetDetectionResult(
            features=features,
            confidence=sum(confidences) / len(confidences),
            model_version=self.version,
            processing_time_ms=0,
            provenance={
                "adapter": "manifest",
                "synthetic": True,
                "pixel_inference": False,
                "scene_reference": scene_reference,
            },
        )


def register_model(
    session,
    *,
    task: str,
    name: str,
    version: str,
    adapter_type: str,
    actor_id,
    framework: str | None = None,
    artifact_uri: str | None = None,
    checksum: str | None = None,
    label_map: dict | None = None,
    metrics: dict | None = None,
    configuration: dict | None = None,
    request_id: str | None = None,
) -> ModelVersion:
    values = {
        "task": task.strip(),
        "name": name.strip(),
        "version": version.strip(),
        "adapter_type": adapter_type.strip(),
    }
    if not all(values.values()):
        raise ModelRegistrationError("Task, name, version, and adapter type are required.")
    existing = session.scalar(
        select(ModelVersion).where(
            ModelVersion.task == values["task"],
            ModelVersion.name == values["name"],
            ModelVersion.version == values["version"],
        )
    )
    if existing is not None:
        return existing
    model = ModelVersion(
        **values,
        framework=framework,
        artifact_uri=artifact_uri,
        checksum=checksum,
        label_map_json=label_map or {},
        metrics_json=metrics or {"status": "not_evaluated"},
        configuration_json=configuration or {},
        registered_by=actor_id,
        status="registered",
    )
    session.add(model)
    session.flush()
    record_audit(
        session,
        actor_id=actor_id,
        action="fra_model_registered",
        entity_type="model_version",
        entity_id=model.id,
        after={"task": model.task, "name": model.name, "version": model.version},
        request_id=request_id,
    )
    return model


def activate_model(
    session,
    model: ModelVersion,
    *,
    actor_id=None,
    request_id: str | None = None,
) -> ModelVersion:
    if model.configuration_json.get("ready") is not True:
        raise ModelRegistrationError("Model is not ready and cannot be activated.")
    active_models = session.scalars(
        select(ModelVersion).where(
            ModelVersion.task == model.task,
            ModelVersion.status == "active",
            ModelVersion.id != model.id,
        )
    ).all()
    for active in active_models:
        active.status = "inactive"
    model.status = "active"
    model.activated_at = datetime.now(timezone.utc)
    record_audit(
        session,
        actor_id=actor_id or model.registered_by,
        action="fra_model_activated",
        entity_type="model_version",
        entity_id=model.id,
        after={"task": model.task, "name": model.name, "version": model.version},
        request_id=request_id,
    )
    session.flush()
    return model


__all__ = [
    "AssetDetectionResult",
    "AssetDetector",
    "DocumentOCRProvider",
    "EntityExtractionResult",
    "FRAEntityExtractor",
    "ImageryProvider",
    "LandCoverClassifier",
    "ManifestAssetDetector",
    "ManifestFRAEntityExtractor",
    "ModelOutputValidationError",
    "ModelRegistrationError",
    "OCRModelResult",
    "activate_model",
    "register_model",
    "validate_model_output",
]
