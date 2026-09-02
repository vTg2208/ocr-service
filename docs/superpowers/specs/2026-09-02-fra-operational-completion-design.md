# Tamil Nadu FRA Operational Completion Design

**Date:** 2026-09-02

**Status:** Approved direction; implementation plan pending user review

**Scope:** Implement P0 recommendations 1–5 and 7–10 from the requirements audit. Recommendation 6, the expanded satellite/WebGIS Atlas interface, is explicitly excluded.

## Objective

Turn the current Tamil Nadu-first FRA foundation into a connected operational workflow in which staff can ingest real FRA records, review extracted fields, create and manage native FRA cases, attach or draw spatial boundaries, run pluggable imagery/model processing, assess spatial evidence, generate historical evidence reports, derive scheme facts, and manage verifier/planner work queues.

The implementation must remain non-adjudicative. OCR, NER, satellite observations, spatial intersections, and DSS recommendations provide evidence or administrative guidance; they never determine legal validity, grant a title, or sanction a benefit without an authorized human action.

## Explicit Exclusion

This programme does not expand the FRA Atlas UI with satellite basemaps, raster overlays, time sliders, opacity controls, or the additional thematic layers described in audit recommendation 6. Existing Atlas behaviour and its village, claim, title, and asset presentation remain intact except for compatibility changes required by shared domain models.

The backend may store raster-scene metadata, derived artifacts, spatial reference layers, and historical observations so a later Atlas project can display them without redesigning the data model.

## Delivery Decomposition

The work is divided into five independently testable workstreams:

1. Unified intake and native FRA case workspace.
2. Real document ingestion and pluggable OCR/NER execution.
3. Geospatial imports and cross-layer spatial evidence.
4. Historical imagery evidence and protected reporting.
5. Automatic DSS facts plus verifier and planner operations.

Each workstream must leave the application usable if subsequent workstreams have not yet been delivered.

## 1. Unified Intake and FRA Case Workspace

### Canonical relationship

The existing `Claim` table remains the legacy patta-registration record. `FRAClaim` remains the authoritative application-domain case. A nullable, unique `legacy_claim_id` relationship continues to link them.

Submitting a patta claim must not silently create or legally classify an FRA case. Instead, it creates an idempotent FRA intake item with state `awaiting_triage`. A reviewer chooses the right type and rights holder/Gram Sabha context before promotion. This avoids treating all land-registry submissions as FRA applications while removing the current invisible API-only bridge.

### Intake states

- `awaiting_triage`
- `ready_for_promotion`
- `promoted`
- `not_fra`
- `duplicate`

Every transition records actor, timestamp, reason, and prior/new values in the audit log. Repeated processing of the same legacy claim must return the same intake item and must never create a second native FRA claim.

### Case workspace

Add a `Cases` section to `/fra` with a searchable queue and a case detail workspace. It exposes existing native capabilities that are currently API-only:

- rights-holder and household/community identity;
- Gram Sabha association;
- IFR/CR/CFR type;
- document and evidence timeline;
- current and previous geometry versions;
- spatial findings;
- lifecycle decisions and reasons;
- title issuance and title versions;
- satellite/history evidence runs;
- DSS recommendations and referrals;
- audit timeline appropriate to the signed-in role.

The case workspace uses explicit forms and confirmation steps for lifecycle transitions and title issuance. A normal `user` may prepare claims and evidence. A `reviewer` or `admin` performs decisions, promotions, asset/evidence verification, and title issuance.

## 2. Document Ingestion and OCR/NER

### Batch upload

The Archive gains a protected batch-import flow accepting multiple PDFs or images. Each batch records source office, district, block/taluk, village, record family, approximate year, provenance, and an idempotency key. Existing file validation, malware scanning, private object storage, checksum detection, and per-user authorization are reused.

Each uploaded file creates a `Document`, `FRAArchiveRecord`, and queued `archive_extract` job. Partial batch failure is allowed: valid records proceed while failed records retain an actionable error and retry history.

### Extraction pipeline

Replace the manifest-only worker choice with an adapter factory selected from the registered `ModelVersion` configuration. Supported execution types are:

- `manifest`, retained only for visibly synthetic fixtures;
- `local_python`, loading an approved local adapter entry point;
- `rest`, calling an authenticated private inference service;
- `artifact`, running an approved local artifact adapter with checksum verification.

The first operational document adapter composes the existing PaddleOCR engine with a Tamil Nadu FRA entity extractor. It emits:

- page-level raw text and confidence;
- detected language per page;
- holder or community name;
- village, block/taluk, and district;
- claim/reference number;
- IFR/CR/CFR type;
- claim status and relevant dates;
- area, survey references, and coordinates when present;
- field-level source spans and confidence;
- warnings and missing required fields.

The deterministic patta extractor remains available for cadastral fields. FRA extraction is a separate schema because FRA forms and decisions contain different concepts.

No extraction result is promoted without reviewer correction/confirmation. Raw document content remains protected and is never copied into DSS inputs or public Atlas properties.

## 3. Geospatial Imports and Spatial Evidence

### Import formats

Add a geospatial import service for:

- zipped ESRI Shapefile;
- GeoJSON;
- KML;
- GeoPackage;
- CSV point coordinates for reference assets only.

The service stages an upload before publication. It detects or requires the CRS, reprojects geometries to EPSG:4326, normalizes polygons to `MultiPolygon`, validates/repairs safe polygon errors, rejects non-polygonal claim boundaries, and reports inserted, updated, duplicate, repaired, invalid, and skipped counts.

Every imported feature carries dataset kind, source authority, source version, source record identifier, license/reference, import batch, and synthetic/authoritative classification. Publishing requires reviewer approval; authoritative publication requires admin approval.

### Reference-layer model

Introduce a versioned `SpatialReferenceFeature` domain with these initial Tamil Nadu kinds:

- `administrative_boundary`;
- `recorded_forest`;
- `protected_area`;
- `water_body`;
- `cadastral_parcel`;
- `infrastructure`;
- `land_use`;
- `potential_fra_area`;
- `groundwater_zone`.

This design stores vector reference features for evaluation without adding them to the Atlas UI in this scope.

### Claim geometry authoring

The case workspace supports GeoJSON upload and Leaflet-based draw/edit for a claim boundary. Saving always creates a new append-only `FRAGeometryVersion`; it never overwrites an earlier geometry.

### Cross-layer evaluation

Extend spatial evaluation to produce versioned findings against:

- active FRA claims and titles;
- recorded forests and protected areas;
- village/administrative boundaries;
- water bodies;
- cadastral parcels;
- infrastructure and land-use reference features;
- potential FRA areas.

Each finding records intersection area, percentage of the candidate, percentage of the reference feature, dataset/source version, policy version, severity, machine-readable reason, and reviewer disposition. Only right-aware FRA claim overlap can produce the existing blocking result. Other intersections are evidence requiring human interpretation.

## 4. Historical Imagery Evidence

### Provider boundary

Add a provider-neutral STAC imagery interface. Initial providers are:

- Landsat-compatible STAC search for older target years such as 2005 and 2010;
- Copernicus Data Space Sentinel-2 STAC search for 2015 onward.

Provider endpoints and credentials are configuration, never hard-coded. Tests use a local fake STAC server and fixture metadata; the automated suite must not depend on external networks.

### Scene and artifact records

Persist immutable scene metadata separately from claim observations:

- provider and collection;
- scene/item identifier;
- acquisition timestamp;
- geometry and cloud cover;
- band/asset references stored as private URIs;
- license/source metadata;
- checksum when downloaded;
- processing state and failure reason.

Derived artifacts record the input scene IDs, claim geometry version, processor/model version, parameters, storage URI, checksum, statistics, quality flags, and creation time.

### Processing boundary

The worker can search scenes, select the best candidate within a configurable date window, crop to the current claim geometry, and invoke a registered analysis adapter. Model weights are not bundled. Until trained models are attached, operational adapters may produce indices and quality metrics but must return `insufficient_model` for unsupported classifications rather than replay synthetic detections.

The adapter output supports forest, agricultural cover, water, and built-up/homestead observations first. Small infrastructure classes remain government/field/manual observations unless an evaluated higher-resolution model is attached.

### Evidence report

Add a protected historical-evidence report for a claim. It includes target year, actual acquisition date, provider, collection, cloud/quality flags, claim geometry version, processing/model version, derived metrics, reviewer disposition, and approved preview images when available. It supports browser print-to-PDF and retains the current no-store/privacy protections.

The report uses neutral evidence language. It must not display a green/red legal-validity badge or say imagery proves tenure.

## 5. Automatic DSS Facts and Operational Dashboards

### Fact builder

Introduce a versioned fact builder that derives DSS inputs instead of accepting unexplained manual facts. Initial facts include:

- active title present;
- claim right type and status;
- verified agricultural, forest, water, and homestead observations;
- water-source absence only when observation coverage is sufficient;
- groundwater/water-stress reference value;
- village socioeconomic attributes;
- recorded infrastructure/service presence;
- source freshness and missing-data flags.

Each fact records value, source entity, source version, observed/effective date, verification state, and derivation version. Missing or stale data remains `unknown`; absence is never inferred merely because no row exists.

`POST /api/fra/dss/evaluate` keeps an admin/test path for explicitly supplied facts, but the staff UI uses a new derive-and-evaluate operation. Recommendations retain exact rule and fact snapshots for reproducibility.

### Scheme catalogue

Create a versioned scheme catalogue separate from executable rule conditions. Initial codes cover Tamil Nadu-relevant configuration for PM-KISAN, MGNREGA, PMAY-G, Jal Jeevan Mission, and DAJGUA convergence. Bundled definitions remain clearly non-authoritative until an administrator records approving authority, effective dates, source reference, and review date.

### Verifier dashboard

Provide queues for:

- archive records needing review;
- FRA intake awaiting triage;
- submitted/remanded claims;
- spatial findings awaiting disposition;
- unverified model/evidence observations;
- failed or overdue processing jobs.

The dashboard links into the case workspace rather than duplicating decision forms.

### Planner dashboard

Provide aggregated, privacy-minimized tables for district, block, and village:

- claims by lifecycle state and right type;
- active titles and granted area;
- verified asset/deficit counts;
- recommendations by scheme and outcome;
- referrals by department and status;
- insufficient-data counts and reasons.

This is an operational dashboard, not the excluded Atlas expansion. It uses tables and summaries without adding satellite/thematic layers to the map.

## API and Data Compatibility

- Existing `/api/claims` and `/api/fra/*` contracts remain available.
- New response fields are additive unless a versioned endpoint is introduced.
- Existing synthetic fixtures continue to work and remain visibly labelled.
- Migrations are forward-only and idempotent where data backfill is required.
- Private source URIs, raw OCR content, model secrets, and claimant identifiers never enter public or unprivileged map responses.

## Failure Handling

- Upload validation or malware failure prevents document creation.
- Batch imports report per-file errors without losing successful files.
- External imagery unavailability yields retryable jobs and no partial evidence rows.
- Invalid CRS or ambiguous geometry remains staged and cannot be published.
- Model adapter/version mismatch fails closed.
- Missing DSS facts produce `insufficient_data`, never `not_recommended`.
- Repeated idempotency keys return the prior result if the request is equivalent and conflict otherwise.

## Testing Strategy

Every production change follows test-first development.

- Unit tests cover extraction normalization, adapter selection, import validation, CRS handling, geometry evaluation, scene selection, fact derivation, and rule evaluation.
- API tests cover permissions, idempotency, failure rollback, privacy filtering, and status codes.
- Worker tests cover retries, adapter/version mismatch, artifact provenance, and no-partial-write behaviour.
- JavaScript tests cover intake triage, case forms, geometry authoring, dashboard filtering, and accessible status messaging.
- Browser tests cover the connected happy path from document upload through case review, geometry, evidence, recommendation, and referral.
- External imagery/model services are replaced with local deterministic fakes in automated tests.

## Delivery and Commit Boundaries

Implementation is delivered through separate meaningful commits, at minimum:

1. FRA intake linkage and migration.
2. Cases API/query support and case workspace.
3. Archive batch upload and real adapter factory.
4. FRA OCR/NER adapter and review evidence.
5. Geospatial staging/import and geometry authoring.
6. Cross-layer spatial evaluation.
7. STAC scene catalogue and imagery job orchestration.
8. Historical evidence report.
9. DSS fact builder and scheme catalogue.
10. Verifier and planner dashboards.
11. End-to-end tests and operational documentation.

Existing uncommitted asset-icon work is outside this programme and must be preserved and committed separately rather than mixed into these commits.

## Completion Criteria

The scope is complete when a Tamil Nadu staff user can, using the browser:

1. upload a real FRA document batch;
2. observe OCR/NER job status and review source-backed extracted fields;
3. triage or promote the record into one native FRA case;
4. manage holder, Gram Sabha, evidence, lifecycle, geometry, and title data under role controls;
5. upload/draw geometry and review claim/reference-layer intersections;
6. request historical scene processing and open a provenance-rich evidence report;
7. derive DSS facts from verified platform data and receive explainable scheme recommendations;
8. create and track a departmental referral;
9. use verifier/planner queues and hierarchical summaries;
10. complete the flow without synthetic manifest inputs or an expanded Atlas imagery UI.
