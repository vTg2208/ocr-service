# Attaching trained FRA models

The Tamil Nadu workflow is deliberately model-independent. Trained weights are not bundled, and an unevaluated model is never assigned a placeholder accuracy. Models produce extraction or observation evidence; they cannot decide legal validity, approve a claim, or sanction a benefit.

## Gateway contracts

Implement the matching protocol in `app/services/model_gateway.py`:

- `DocumentOCRProvider.recognize(document_reference, context) -> OCRModelResult`
- `FRAEntityExtractor.extract(document_reference, manifest) -> EntityExtractionResult`
- `LandCoverClassifier.classify(scene_reference, geometry, context) -> AssetDetectionResult`
- `AssetDetector.detect(scene_reference, geometry, context) -> AssetDetectionResult`

Every adapter exposes a stable `version`, processing time, confidence in the range 0..1 or `null`, and provenance. Asset features use only the supported classes `agricultural_cover`, `forest_cover`, `water_body`, and `homestead`. `validate_model_output` rejects decision-like keys such as `valid`, `approved`, `eligibility`, and `sanctioned` anywhere in the output.

The built-in manifest adapters replay visibly synthetic fixtures and explicitly record `pixel_inference: false`. They are development adapters, not trained models.

## Adapter configurations

Keep credentials in environment variables or a secret manager, never in the registry JSON.

Allow-listed local entity adapter metadata:

```json
{
  "task": "entity_extraction",
  "name": "tn-fra-ner",
  "version": "2026.09.0",
  "adapter_type": "local_python",
  "framework": "python",
  "artifact_uri": null,
  "checksum": "sha256-of-reviewed-artifact",
  "metrics": {"status": "evaluated", "macro_f1": 0.82, "evaluation_set": "tn-held-out-v1"},
  "configuration": {"runner": "tamil_nadu_fra_regex_v1", "ready": true}
}
```

REST entity adapter metadata:

```json
{
  "task": "entity_extraction",
  "name": "tn-fra-entities",
  "version": "2026.09.0",
  "adapter_type": "rest",
  "framework": "onnx-runtime-service",
  "artifact_uri": null,
  "metrics": {"status": "evaluated", "macro_f1": 0.76},
  "configuration": {"endpoint": "https://models.example.gov.in/fra/entities", "allowed_hosts": ["models.example.gov.in"], "timeout_seconds": 30, "ready": true}
}
```

Historical-evidence processing uses a separately deployed REST model and the same strict version check:

```json
{
  "task": "historical_evidence",
  "name": "tn-fra-history",
  "version": "2026.09.0",
  "adapter_type": "rest",
  "framework": "model-service",
  "metrics": {"status": "evaluated", "evaluation_set": "tn-held-out-history-v1"},
  "configuration": {"endpoint": "https://models.example.gov.in/fra/history", "allowed_hosts": ["models.example.gov.in"], "timeout_seconds": 60, "ready": true}
}
```

The worker now instantiates the allow-listed Tamil Nadu local entity extractor, an allow-listed REST entity extractor, and an allow-listed REST historical processor. It fails closed on endpoint, host, readiness, or version mismatch. Asset manifests remain restricted to visibly synthetic fixture processing until a separately evaluated asset adapter is implemented and approved; no arbitrary Python entrypoint or artifact import is allowed.

The REST historical response must contain base64 artifact bytes, `statistics`, string `quality_flags`, matching `processor_version` and `model_version`, and non-adjudicative provenance. The service limits decoded artifacts to 25 MB and rejects automated legal conclusions.

## Register, activate, and run

Registration and activation require authenticated admin/reviewer sessions. The equivalent protected endpoints are:

```text
POST /api/fra/models
POST /api/fra/models/{model_id}/activate
GET  /api/fra/models?task=asset_detection&status=active
python -m scripts.run_fra_jobs --max-jobs 20
```

Activation fails unless `configuration.ready` is `true`. Only one version per task remains active. Preserve the checksum, label map, metrics, evaluation-set reference, runtime configuration, and activation audit event.

## Evaluation files

Both input files are JSON arrays with the same length:

```powershell
python -m scripts.evaluate_fra_models --task ocr --labels labels.json --predictions predictions.json
python -m scripts.evaluate_fra_models --task entity_extraction --labels labels.json --predictions predictions.json
python -m scripts.evaluate_fra_models --task asset_classification --labels labels.json --predictions predictions.json
```

OCR samples use `{"text": "..."}`. Entity samples are field dictionaries. Asset samples use `{"label": "water_body"}`; predictions may also contain an independently calculated `iou` between 0 and 1. Empty labels return `status: "not_evaluated"`.

## Operational boundary

The current profile accepts only Tamil Nadu (`TN`). Authoritative village boundaries, legally approved rules/catalogue versions, labelled evaluation sets, access approvals, and reviewed model documentation must replace every synthetic fixture before operational use. The UI must continue to display these exact meanings:

- Model and satellite observations are supporting evidence and do not determine legal validity.
- DSS recommendations are advisory and do not approve or sanction benefits.
