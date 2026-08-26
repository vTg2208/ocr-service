# Tamil Nadu FRA Platform Completion Design

## Purpose

Complete AranyaSetu as an academically defensible, end-to-end final-year project for Forest Rights Act archive digitization, WebGIS visualization, model-assisted asset mapping, and explainable scheme convergence. The first supported state profile is Tamil Nadu. Other states can be added later through reference-data and validation adapters without changing the core domain.

The project remains a research and workflow prototype. It must demonstrate real software behavior, reproducible processing, model attachment, provenance, human review, and measurable evaluation. It must not claim that synthetic data, unvalidated models, satellite observations, or demonstration scheme rules are legally or scientifically authoritative.

## Approved Direction

Use a modular monolith with explicit ports and adapters:

- FastAPI provides authenticated APIs and server-hosted workspaces.
- SQLAlchemy and Alembic persist the FRA domain, processing history, model lineage, spatial features, and planning decisions.
- PostgreSQL/PostGIS is the production-shaped database; SQLite and Shapely remain the deterministic development/test path.
- A database-backed worker executes queued OCR, entity-extraction, asset-inference, and report jobs without requiring Redis or Celery.
- Model adapters may run as local Python, ONNX/PyTorch artifacts, local model servers, or remote REST services.
- Leaflet renders the FRA Atlas using privacy-safe GeoJSON APIs.
- The existing patta workflow and P0 `/api/fra/*` foundation remain backward compatible.

Microservices are intentionally deferred. The model and job boundaries make later extraction possible without imposing distributed-system overhead on the final-year project.

## State Strategy

Tamil Nadu is the only fully configured state profile in this phase. It defines:

- the hierarchy `state -> district -> block/taluk -> village`;
- canonical state code `TN` and state name `Tamil Nadu`;
- Tamil/English archive labels and searchable administrative fields;
- synthetic, visibly non-authoritative village boundaries and FRA examples;
- validation that prevents an unsupported state from being silently treated as Tamil Nadu.

State-specific behavior is exposed through a `StateProfile` interface and a registry. APIs return a clear `unsupported_state` response for missing profiles. Adding another state requires a profile, authoritative-or-synthetic reference data with provenance, and focused tests.

## Product Workspaces

The selected interface is a workflow-workspace application with persistent navigation and shared claim/village context.

### Archive

The archive supports single and batch intake, processing status, OCR/entity-extraction runs, side-by-side source evidence and standardized fields, reviewer corrections, duplicate warnings, and search. Search filters include claim number, rights-holder display name, district, block/taluk, village, right type, claim status, and year.

Only reviewed archive fields can be promoted into the native FRA claim domain. The source document, every extraction run, corrections, and promotion link remain traceable.

### FRA Atlas

The Atlas displays IFR, CR, CFR, granted-title, village-boundary, asset, and observation layers. It supports Tamil Nadu district, block/taluk, village, tribal-group, right-type, lifecycle-status, and year filters. It provides progress counts and area summaries at supported administrative levels.

Normal authenticated users receive privacy-safe map features without rights-holder external references or private document/source URIs. Reviewer/admin detail views can access protected case metadata. Map filters never mutate claims.

### Assets

The asset workspace stores agricultural land, forest cover, water body, and homestead point/polygon observations. Every result references a model/inference version or a declared manual source, acquisition time, confidence, provenance, and verification state. Reviewers may verify, reject, or correct an observation; earlier model output remains immutable.

Until trained models are attached, a deterministic manifest adapter provides clearly labelled synthetic results. The UI and reports state that observations are supporting evidence, not legal validity decisions.

### DSS Planner

The planner extends the P0 rule engine with recommendation lists grouped by holder, village, and administrative area. A recommendation can become a human-managed referral with assigned department, status, notes, and disposition history. It never approves, sanctions, or transmits a benefit.

Priority views may identify missing water, housing, or livelihood facts using versioned demonstration rules. Every item exposes the rules, inputs, missing facts, reasons, source references, and model/observation provenance used.

### Reports

Protected printable HTML reports cover an archive record, an FRA case/evidence timeline, an asset-observation summary, and a village planning summary. Browser print-to-PDF is the supported PDF path, avoiding a second document-rendering stack. Reports include creation time, filters, provenance, synthetic/demo labels, and mandatory legal/advisory warnings.

## Persistence Model

### `FRAImportBatch`

Stores source label, state profile, importer, status, idempotency key, counts, provenance, error summary, and timestamps.

### `FRAArchiveRecord`

Links a private `Document` to a batch and optionally an `FRAClaim`. Stores the legacy reference, review state, normalized searchable fields, review metadata, duplicate fingerprint, and provenance. Core search fields are explicit columns; flexible extraction evidence remains JSON.

### `FRAExtractionRun`

Stores archive record, OCR model version, entity-extractor model version, raw text, standardized output, field evidence, confidence, processing time, and creation time. Runs are append-only. The archive record points to reviewed values rather than overwriting a run.

### `ProcessingJob`

Stores task type, entity reference, state (`queued`, `running`, `completed`, `failed`, `quarantined`), attempts, maximum attempts, idempotency key, payload, result summary, error code/message, worker identity, and timestamps. A worker atomically claims eligible jobs. Retriable and permanent errors are distinguished.

### `ModelVersion`

Stores task, adapter type, name, semantic version, framework, artifact URI, checksum, label map, metrics, configuration, active status, and registration actor/time. Model metadata is usable before an artifact is available; inactive or unready versions cannot run jobs.

### `InferenceRun`

Stores model version, job, input entity, input snapshot, output, confidence, processing time, provenance, and state. It never stores a legal-validity or benefit-approval conclusion.

### `FRAVillageProfile`

Stores Tamil Nadu administrative names/codes, village boundary, tribal-group metadata, socio-economic facts, provenance, and reference-data version. The natural key is state/district/block/village code.

### `AssetFeature`

Stores village/claim association, asset class, WGS84 point or polygon geometry, observed value, acquisition date, confidence, inference/manual source, provenance, and verification fields. Model-created features are immutable; corrections create a superseding feature.

### `DSSReferral`

Links a recommendation to a target department, priority, status, assignee, notes, and append-only disposition history. It represents administrative follow-up only.

### `ReportArtifact`

Stores report type, subject, parameters, private storage key when exported, generation job, actor, content hash, and timestamp.

## Model Gateway

Stable contracts are provided for:

- `DocumentOCRProvider`
- `FRAEntityExtractor`
- `ImageryProvider`
- `LandCoverClassifier`
- `AssetDetector`

Adapters return typed results containing model identity, output values, confidence, processing time, and provenance. They cannot directly create a decision, change claim status, verify evidence, issue a title, create a DSS referral, or mark a benefit as approved.

The initial adapters are:

- the existing OCR engine wrapper for document OCR;
- a deterministic Tamil Nadu manifest entity extractor for synthetic demonstrations;
- the existing local satellite manifest provider;
- deterministic asset results for synthetic scenes.

Future trained models attach by registering a `ModelVersion` and implementing the matching contract. API routes and domain services remain unchanged.

## Processing Flow

### Archive intake

1. An authenticated staff user uploads a supported document and Tamil Nadu metadata.
2. The system stores the file privately, creates an import batch/record, and enqueues extraction idempotently.
3. The worker runs OCR and entity extraction through registered adapters.
4. Results become an append-only extraction run and the record enters `needs_review`.
5. A reviewer accepts or corrects normalized fields.
6. The reviewed record can be promoted into the existing FRA claim service.

### Asset inference

1. A claim or village with geometry requests an asset job for a registered scene/model.
2. The imagery and model adapters return typed observations.
3. The worker stores the inference run and unverified asset features/evidence in one transaction.
4. A reviewer verifies, rejects, or supersedes each feature.
5. Atlas and DSS views display the verification state and source version.

### Planning

1. Declared, title, village, and explicitly permitted observation facts are assembled.
2. The constrained P0 rules produce versioned advisory recommendations.
3. A planner may create a referral and manage its disposition.
4. No action is sent to a government system in this phase.

## APIs

New protected APIs are grouped under `/api/fra`:

```text
POST   /archive/batches
POST   /archive/records
GET    /archive/records
GET    /archive/records/{record_id}
POST   /archive/records/{record_id}/review
POST   /archive/records/{record_id}/promote

GET    /jobs
GET    /jobs/{job_id}
POST   /jobs/{job_id}/retry

POST   /models
GET    /models
POST   /models/{model_id}/activate

GET    /atlas/features
GET    /atlas/summary
GET    /villages
GET    /villages/{village_id}

POST   /assets/inference-jobs
GET    /assets
POST   /assets/{asset_id}/review

GET    /dss/recommendations
POST   /dss/recommendations/{recommendation_id}/referrals
PATCH  /dss/referrals/{referral_id}

GET    /reports/archive/{record_id}
GET    /reports/claims/{claim_id}
GET    /reports/villages/{village_id}
```

Archive intake, review, model registration/activation, asset review, referral mutation, and report export are audited. Reviewer/admin roles are required for review and model activation; admin is required for model registration and state-reference imports.

## Interface

The protected `/fra` application uses the existing authoritative, calm, precise AranyaSetu visual language. A persistent navigation rail exposes Archive, FRA Atlas, Assets, DSS Planner, and Reports. A shared context bar retains the selected Tamil Nadu district, block/taluk, village, claim, or archive record between workspaces.

The Archive uses a review queue and side-by-side source/extraction layout. The Atlas uses a large map with collapsible filters and a case detail drawer. Assets use a time-oriented observation table paired with the map. The Planner uses explainable priority lists rather than traffic-light legal-validity signals. Every screen supports keyboard access, visible focus, 44-pixel touch targets, empty/error/loading states, and a narrow mobile layout without removing critical functionality.

The existing `/land-mapping` interface remains available and unchanged except for a link into the FRA application.

## Safety, Privacy, and Error Handling

- Documents, model artifacts, source URIs, and exported reports use private storage.
- Rights-holder external references never appear in normal-user atlas responses, logs, or aggregate reports.
- Every automated output is labelled with its model/adapter, version, confidence, provenance, and verification state.
- Satellite and model outputs are supporting observations and do not determine legal validity.
- DSS outputs are advisory and do not approve or sanction benefits.
- Failed jobs cannot leave partial extraction runs, inference runs, evidence, assets, referrals, or reports.
- Unsupported states, missing models, unavailable scenes, invalid geometries, duplicate imports, low-confidence results, and stale review attempts have explicit error codes and recoverable UI states.
- Audit events contain metadata and identifiers, not raw document bytes or sensitive model artifacts.
- Synthetic Tamil Nadu records, boundaries, scenes, rules, and metrics are visibly labelled at storage, API, UI, and report boundaries.

## Testing and Evaluation

### Automated verification

- migration and model constraints;
- Tamil Nadu profile normalization and unsupported-state behavior;
- archive ingestion, duplicate/idempotency behavior, extraction versioning, review, search, and promotion;
- atomic job claiming, retries, quarantine, and rollback;
- model registration, activation, adapter contracts, and provenance;
- atlas privacy, filters, GeoJSON, progress counts, and area summaries;
- asset inference, verification, supersession, and prohibition of legal conclusions;
- DSS referral authorization, history, and advisory language;
- report privacy and mandatory warnings;
- browser logic, accessibility semantics, responsive layouts, and console health;
- all existing P0 and legacy regressions.

### Academic evaluation

The repository will include repeatable evaluation commands and result schemas for OCR character/word error rate, entity precision/recall/F1, asset-class precision/recall/F1/IoU, job latency, and API/browser behavior. Until trained models and labelled datasets are supplied, model metrics are recorded as `not_evaluated`; synthetic fixture accuracy must never be presented as real model performance.

## Seed Data

Provide a small Tamil Nadu synthetic demonstration pack containing:

- administrative profiles and village boundaries;
- IFR, CR, and CFR archive examples with reviewed and pending states;
- synthetic documents/manifests rather than real personal records;
- time-separated local scene manifests;
- asset features with verified and unverified examples;
- demo water, housing, and livelihood rules/referrals.

Every seed record includes `synthetic: true`, source/version provenance, and a user-facing warning.

## Completion Criteria

Completion means the four problem-statement gaps can be demonstrated end to end for Tamil Nadu:

1. ingest, extract, review, search, and promote a legacy FRA record;
2. view claims, titles, villages, status, and assets in a filterable FRA Atlas;
3. run or replay a versioned asset-model job and review its supporting output;
4. evaluate explainable scheme recommendations and track a human referral;
5. generate privacy-safe printable case and village reports;
6. attach a future trained model through a documented adapter without changing domain or route code.

Real government integrations, authoritative boundaries, operational scheme sanctioning, real-time satellite feeds, IoT, claimant self-service, and claims of model/legal validity remain outside this phase and require external data, credentials, validation, and authority approval.
