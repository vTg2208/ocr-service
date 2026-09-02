"""Strict, allow-listed attachment points for trained FRA entity models."""

import json
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.services.fra_entity_extraction import TamilNaduFRAExtractor
from app.services.model_gateway import (
    EntityExtractionResult,
    ManifestFRAEntityExtractor,
    ModelOutputValidationError,
    ModelRegistrationError,
    validate_model_output,
)


ENTITY_TASKS = {"entity_extraction", "fra_entity_extraction", "archive_extraction"}
LOCAL_RUNNERS = {"tamil_nadu_fra_regex_v1": TamilNaduFRAExtractor}


def _default_rest_transport(endpoint: str, payload: dict, timeout: float) -> dict:
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - endpoint is validated.
        return json.loads(response.read().decode("utf-8"))


def _confidence(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelOutputValidationError("confidence must be numeric.")
    result = float(value)
    if not 0 <= result <= 1:
        raise ModelOutputValidationError("confidence must be between 0 and 1.")
    return result


class RESTFRAEntityExtractor:
    def __init__(self, model, transport):
        self.version = model.version
        self.model_id = str(model.id)
        self.configuration = dict(model.configuration_json or {})
        self.transport = transport
        endpoint = str(self.configuration.get("endpoint") or "").strip()
        parsed = urlparse(endpoint)
        allowed_hosts = {
            str(host).strip().casefold()
            for host in self.configuration.get("allowed_hosts", [])
            if str(host).strip()
        }
        local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
        if parsed.scheme != "https" and not local_http:
            raise ModelRegistrationError("REST model endpoint must use HTTPS.")
        if not parsed.hostname or parsed.hostname.casefold() not in allowed_hosts:
            raise ModelRegistrationError("REST model host is not allow-listed.")
        self.endpoint = endpoint
        self.endpoint_host = parsed.hostname
        self.timeout = float(self.configuration.get("timeout_seconds", 30))
        if not 0 < self.timeout <= 120:
            raise ModelRegistrationError("REST model timeout must be between 0 and 120 seconds.")

    def extract(self, document_reference: str, manifest: dict) -> EntityExtractionResult:
        if not isinstance(manifest, dict) or not isinstance(manifest.get("raw_text"), str):
            raise ModelOutputValidationError("OCR text is required for REST entity extraction.")
        response = self.transport(
            self.endpoint,
            {"raw_text": manifest["raw_text"], "state_code": "TN"},
            self.timeout,
        )
        if not isinstance(response, dict):
            raise ModelOutputValidationError("REST model response must be an object.")
        if response.get("model_version") != self.version:
            raise ModelRegistrationError("Attached model version mismatch.")
        fields = response.get("fields")
        evidence = response.get("field_evidence")
        if not isinstance(fields, dict) or not isinstance(evidence, dict):
            raise ModelOutputValidationError("REST model fields and field evidence must be objects.")
        validate_model_output(fields)
        processing_time = response.get("processing_time_ms", 0)
        if isinstance(processing_time, bool) or not isinstance(processing_time, int) or processing_time < 0:
            raise ModelOutputValidationError("processing_time_ms must be a non-negative integer.")
        provenance = response.get("provenance") or {}
        if not isinstance(provenance, dict):
            raise ModelOutputValidationError("REST model provenance must be an object.")
        return EntityExtractionResult(
            fields=fields,
            field_evidence=evidence,
            confidence=_confidence(response.get("confidence")),
            model_version=self.version,
            processing_time_ms=processing_time,
            provenance={
                **provenance,
                "adapter": "rest",
                "endpoint_host": self.endpoint_host,
                "model_id": self.model_id,
                "legal_role": "unverified_extraction",
            },
            warnings=list(response.get("warnings") or []),
        )


def create_entity_extractor(model, *, rest_transport=None):
    if model.task not in ENTITY_TASKS:
        raise ModelRegistrationError("Model task is not FRA entity extraction.")
    configuration = dict(model.configuration_json or {})
    if configuration.get("ready") is not True or model.status != "active":
        raise ModelRegistrationError("Model is not ready and active.")
    adapter_type = str(model.adapter_type or "").strip().casefold()
    if adapter_type == "manifest":
        if configuration.get("synthetic") is not True:
            raise ModelRegistrationError("Manifest adapters are limited to synthetic records.")
        return ManifestFRAEntityExtractor(model.version)
    if adapter_type == "local_python":
        runner_name = str(configuration.get("runner") or "").strip()
        runner = LOCAL_RUNNERS.get(runner_name)
        if runner is None:
            raise ModelRegistrationError("Local model runner is not allow-listed.")
        return runner(model.version)
    if adapter_type == "rest":
        return RESTFRAEntityExtractor(model, rest_transport or _default_rest_transport)
    raise ModelRegistrationError(f"Unsupported adapter: {model.adapter_type}")


__all__ = ["RESTFRAEntityExtractor", "create_entity_extractor"]
