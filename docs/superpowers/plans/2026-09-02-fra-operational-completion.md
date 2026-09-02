# Tamil Nadu FRA Operational Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect real document intake, native FRA casework, geospatial evidence, historical imagery processing, automatic DSS facts, and operational dashboards while leaving the expanded Atlas imagery UI out of scope.

**Architecture:** Add a focused operational domain alongside the existing FRA foundation, preserving current APIs and append-only case history. New routes call small services for intake, extraction adapters, geospatial staging, STAC scene orchestration, evidence reporting, fact derivation, and dashboard aggregation; workers execute all expensive or external processing through versioned jobs.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL/PostGIS, SQLite/Shapely tests, PaddleOCR, GDAL-compatible command adapters, STAC JSON APIs, private local/S3 storage, Leaflet, static HTML/CSS/JavaScript, pytest, Node test runner.

**Spec:** `docs/superpowers/specs/2026-09-02-fra-operational-completion-design.md`

## Global Constraints

- Tamil Nadu (`TN`) is the only enabled state profile.
- Recommendation 6 is excluded: do not add satellite basemaps, raster overlays, time sliders, opacity controls, or new thematic layers to the FRA Atlas UI.
- Automated outputs are supporting evidence or advisory recommendations and cannot determine legal validity, issue a title, or sanction a benefit.
- Existing `/api/claims` and `/api/fra/*` contracts remain available; additions are backward compatible.
- Every external source, model output, geometry, fact, and recommendation retains versioned provenance.
- Raw OCR text, private URIs, credentials, and claimant identity do not enter unprivileged map or dashboard responses.
- Synthetic manifest adapters remain available only for visibly synthetic fixtures.
- Every production behaviour is developed test-first and committed in a coherent, independently testable change.

---

### Task 1: Operational Domain Models and Migration

**Files:**
- Create: `app/db/fra_operational_models.py`
- Create: `migrations/versions/20260902_0005_fra_operational.py`
- Modify: `app/db/__init__.py`
- Modify: `app/db/fra_models.py`
- Modify: `app/db/fra_completion_models.py`
- Test: `tests/test_fra_operational_models.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `FRAIntakeItem`, `SpatialImportBatch`, `SpatialReferenceFeature`, `ImagerySceneRecord`, `ImageryArtifact`, `DSSFactSnapshot`, and `SchemeCatalogEntry` SQLAlchemy models.
- Consumes: existing `Claim`, `FRAClaim`, `FRAGeometryVersion`, `ProcessingJob`, `User`, and GeoJSON multipolygon type.

- [ ] **Step 1: Write the failing model and migration tests**

```python
def test_legacy_claim_has_one_idempotent_fra_intake_item(session):
    item = FRAIntakeItem(legacy_claim_id=claim.id, state="awaiting_triage", created_by=user.id)
    session.add(item); session.commit()
    session.add(FRAIntakeItem(legacy_claim_id=claim.id, state="awaiting_triage", created_by=user.id))
    with pytest.raises(IntegrityError):
        session.commit()

def test_operational_migration_creates_required_tables(alembic_connection):
    assert {
        "fra_intake_items", "spatial_import_batches", "spatial_reference_features",
        "imagery_scenes", "imagery_artifacts", "dss_fact_snapshots", "scheme_catalog_entries",
    } <= inspect(alembic_connection).get_table_names()
```

- [ ] **Step 2: Run tests and verify failure because the models/tables do not exist**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_operational_models.py tests/test_migrations.py`

- [ ] **Step 3: Implement focused models, constraints, relationships, JSON provenance, timestamps, and migration**

Key constraints:

```python
UniqueConstraint("legacy_claim_id", name="uq_fra_intake_legacy_claim")
UniqueConstraint("source_authority", "source_version", "source_record_id", name="uq_spatial_source_record")
UniqueConstraint("provider", "collection", "scene_id", name="uq_imagery_scene")
UniqueConstraint("claim_id", "derivation_version", "idempotency_key", name="uq_dss_fact_snapshot")
UniqueConstraint("scheme_code", "version", name="uq_scheme_catalog_version")
```

- [ ] **Step 4: Run model and migration tests, then full Python tests**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_operational_models.py tests/test_migrations.py`

Run: `.\venv\Scripts\python.exe -m pytest -q`

- [ ] **Step 5: Commit**

```powershell
git add app/db migrations/versions/20260902_0005_fra_operational.py tests/test_fra_operational_models.py tests/test_migrations.py
git commit -m "feat: add FRA operational domain models"
```

### Task 2: Legacy Claim Intake and Reviewer Promotion

**Files:**
- Create: `app/models/fra_intake_schemas.py`
- Create: `app/services/fra_intake.py`
- Create: `app/api/fra_intake_routes.py`
- Modify: `app/services/claim_service.py`
- Modify: `app/api/patta_routes.py`
- Modify: `app/main.py`
- Test: `tests/test_fra_intake.py`
- Test: `tests/test_fra_intake_api.py`
- Modify: `tests/test_claim_service.py`

**Interfaces:**
- Produces: `ensure_intake_for_legacy_claim(session, claim, actor_id) -> FRAIntakeItem` and `promote_intake(session, item, payload, actor_id) -> FRAClaim`.
- Produces routes: `GET /api/fra/intake`, `GET /api/fra/intake/{id}`, `PATCH /api/fra/intake/{id}`, `POST /api/fra/intake/{id}/promote`.
- Consumes: `promote_legacy_claim()` and existing role/audit utilities.

- [ ] **Step 1: Write failing service tests for idempotent intake, `not_fra`, duplicate, and promotion behaviour**

```python
first = ensure_intake_for_legacy_claim(session, legacy, actor_id=user.id)
second = ensure_intake_for_legacy_claim(session, legacy, actor_id=user.id)
assert first.id == second.id
assert first.state == "awaiting_triage"

claim = promote_intake(session, first, PromotionInput(
    right_type="IFR", rights_holder_id=holder.id, gram_sabha_id=None,
), actor_id=reviewer.id)
assert claim.legacy_claim_id == legacy.id
assert first.state == "promoted"
```

- [ ] **Step 2: Run tests and verify expected missing-service failure**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_intake.py`

- [ ] **Step 3: Implement the intake service and hook successful legacy claim creation into it in the same transaction**

- [ ] **Step 4: Write failing API permission, filtering, transition, conflict, and idempotency tests**

```python
assert client.post(f"/api/fra/intake/{item.id}/promote", headers=user_headers, json=payload).status_code == 403
response = client.post(f"/api/fra/intake/{item.id}/promote", headers=reviewer_headers, json=payload)
assert response.status_code == 201
assert response.json()["legacy_claim_id"] == str(legacy.id)
```

- [ ] **Step 5: Implement schemas/routes and register the router**

- [ ] **Step 6: Run intake, claim, land API, and full tests**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_intake.py tests/test_fra_intake_api.py tests/test_claim_service.py tests/test_land_api.py`

- [ ] **Step 7: Commit**

```powershell
git add app/models/fra_intake_schemas.py app/services/fra_intake.py app/api/fra_intake_routes.py app/services/claim_service.py app/api/patta_routes.py app/main.py tests
git commit -m "feat: connect patta claims to FRA intake"
```

### Task 3: Native FRA Case Query API and Case Workspace

**Files:**
- Create: `app/api/fra_case_routes.py`
- Create: `app/services/fra_cases.py`
- Create: `app/static/fra/cases.js`
- Modify: `app/main.py`
- Modify: `app/static/fra/index.html`
- Modify: `app/static/fra/app.js`
- Modify: `app/static/fra/styles.css`
- Test: `tests/test_fra_case_api.py`
- Test: `tests/fra_cases_ui.test.js`
- Modify: `tests/test_fra_workspace_ui.py`

**Interfaces:**
- Produces: `GET /api/fra/cases`, detailed `GET /api/fra/cases/{id}`, and role-filtered audit timeline.
- Consumes: existing rights-holder, evidence, geometry, transition, title, recommendation, referral, and audit APIs.

- [ ] **Step 1: Write failing case-list/detail API tests**

```python
response = client.get("/api/fra/cases?status=submitted&right_type=IFR", headers=reviewer_headers)
assert response.status_code == 200
assert response.json()["items"][0]["claim_number"] == "TN-IFR-001"
assert "private_uri" not in json.dumps(response.json())
```

- [ ] **Step 2: Verify failure, implement privacy-safe query service/routes, and run API tests**

- [ ] **Step 3: Write failing UI tests for Cases navigation, intake triage, detail tabs, geometry/evidence/status/title forms, and role-safe actions**

```javascript
test('case workspace keeps claim context while switching tabs', () => {
  const next = FRACasesUI.selectCase(FRACasesUI.initialState(), 'claim-1');
  assert.equal(next.selectedCaseId, 'claim-1');
  assert.equal(FRACasesUI.canIssueTitle({ role: 'reviewer', status: 'granted' }), true);
});
```

- [ ] **Step 4: Verify UI test failure and implement the Cases section using existing API helpers and accessible forms**

- [ ] **Step 5: Run Python, Node, and HTML structure tests**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_case_api.py tests/test_fra_workspace_ui.py`

Run: `node --test tests/fra_cases_ui.test.js tests/fra_workspace_ui.test.js`

- [ ] **Step 6: Commit**

```powershell
git add app/api/fra_case_routes.py app/services/fra_cases.py app/main.py app/static/fra tests/test_fra_case_api.py tests/fra_cases_ui.test.js tests/test_fra_workspace_ui.py
git commit -m "feat: add native FRA case workspace"
```

### Task 4: Archive Batch Upload and Document Processing

**Files:**
- Create: `app/services/fra_document_intake.py`
- Modify: `app/api/fra_archive_routes.py`
- Modify: `app/models/fra_completion_schemas.py`
- Modify: `app/static/fra/index.html`
- Modify: `app/static/fra/app.js`
- Modify: `app/static/fra/archive.js`
- Modify: `app/static/fra/styles.css`
- Test: `tests/test_fra_document_intake.py`
- Modify: `tests/test_fra_archive_api.py`
- Modify: `tests/fra_workspace_ui.test.js`

**Interfaces:**
- Produces multipart `POST /api/fra/archive/batch-upload` returning per-file record/job/error results.
- Consumes existing upload validation, ClamAV scanner, private storage, `create_import_batch`, `create_archive_record`, and `enqueue_job`.

- [ ] **Step 1: Write failing service tests for mixed-success batches, duplicate checksums, malware failure, idempotency, and cleanup on rollback**

- [ ] **Step 2: Verify failure and implement `ingest_archive_batch()` with one transaction boundary per accepted file and an aggregate batch result**

- [ ] **Step 3: Write failing multipart API tests**

```python
response = client.post(
    "/api/fra/archive/batch-upload",
    headers={**headers, "Idempotency-Key": "tn-batch-1"},
    data={"source_office": "District Tribal Welfare Office", "district": "Salem"},
    files=[("files", ("claim.pdf", pdf_bytes, "application/pdf"))],
)
assert response.status_code == 202
assert response.json()["accepted"] == 1
```

- [ ] **Step 4: Implement route, request limits, and Archive upload UI with per-file progress/errors**

- [ ] **Step 5: Run archive, upload, malware, storage, and UI tests**

- [ ] **Step 6: Commit**

```powershell
git add app/services/fra_document_intake.py app/api/fra_archive_routes.py app/models/fra_completion_schemas.py app/static/fra tests
git commit -m "feat: add FRA archive batch ingestion"
```

### Task 5: Real OCR/NER Adapter Factory

**Files:**
- Create: `app/services/fra_adapter_factory.py`
- Create: `app/services/fra_entity_extraction.py`
- Modify: `app/services/model_gateway.py`
- Modify: `app/services/fra_job_handlers.py`
- Modify: `app/services/fra_archive.py`
- Modify: `app/models/fra_completion_schemas.py`
- Test: `tests/test_fra_adapter_factory.py`
- Test: `tests/test_fra_entity_extraction.py`
- Modify: `tests/test_processing_jobs.py`
- Modify: `tests/test_fra_archive.py`

**Interfaces:**
- Produces: `create_entity_extractor(model: ModelVersion) -> FRAEntityExtractor` and `TamilNaduFRAExtractor.extract(document_reference, manifest) -> EntityExtractionResult`.
- Consumes: private document storage, existing PaddleOCR/PDF processor, and registered model configuration.

- [ ] **Step 1: Write failing factory tests for `manifest`, `local_python`, `rest`, unsupported type, readiness, and version mismatch**

```python
extractor = create_entity_extractor(local_model)
assert extractor.version == local_model.version
with pytest.raises(ModelRegistrationError, match="Unsupported adapter"):
    create_entity_extractor(unknown_model)
```

- [ ] **Step 2: Verify failure and implement a strict allow-listed adapter factory; never import an arbitrary user-supplied module path**

- [ ] **Step 3: Write failing extraction tests using Tamil/English fixture text and expected field evidence**

```python
result = extractor.extract_text("Claim No: TN/IFR/12\nVillage: Kottur\nStatus: Pending")
assert result.fields["claim_number"] == "TN/IFR/12"
assert result.fields["village"] == "Kottur"
assert result.field_evidence["village"]["text"] == "Village: Kottur"
```

- [ ] **Step 4: Implement FRA normalization separately from the patta parser, with missing-field warnings and no inferred legal result**

- [ ] **Step 5: Change `archive_extract` handler to resolve the registered adapter and process the stored document; keep manifest behaviour only for synthetic records**

- [ ] **Step 6: Run model, extraction, job, archive, OCR, and full tests**

- [ ] **Step 7: Commit**

```powershell
git add app/services/fra_adapter_factory.py app/services/fra_entity_extraction.py app/services/model_gateway.py app/services/fra_job_handlers.py app/services/fra_archive.py app/models/fra_completion_schemas.py tests
git commit -m "feat: run versioned FRA OCR and entity adapters"
```

### Task 6: Geospatial Staging, Publication, and Claim Geometry Authoring

**Files:**
- Create: `app/models/fra_geospatial_schemas.py`
- Create: `app/services/fra_geospatial_import.py`
- Create: `app/api/fra_geospatial_routes.py`
- Modify: `app/main.py`
- Modify: `app/static/fra/cases.js`
- Modify: `app/static/fra/index.html`
- Modify: `app/static/fra/styles.css`
- Modify: `requirements.txt`
- Test: `tests/test_fra_geospatial_import.py`
- Test: `tests/test_fra_geospatial_api.py`
- Modify: `tests/fra_cases_ui.test.js`

**Interfaces:**
- Produces staged import/upload/preview/publish routes under `/api/fra/geospatial/imports`.
- Produces claim geometry upload/draw UI calling existing append-only geometry API.
- Consumes Fiona/GDAL-compatible readers behind an injected `VectorDatasetReader`; GeoJSON reader is built in and tests do not require system GDAL.

- [ ] **Step 1: Write failing format, CRS, geometry repair, duplicate, provenance, and publish-permission tests using injected fixture readers**

- [ ] **Step 2: Verify failure and implement staging service plus built-in GeoJSON reader**

- [ ] **Step 3: Add optional Shapefile/KML/GeoPackage readers using a pinned geospatial package and explicit archive path validation**

- [ ] **Step 4: Write failing API tests for preview, publish, invalid CRS, unsupported geometry, reviewer/admin boundaries, and synthetic/authoritative classification**

- [ ] **Step 5: Implement routes and add GeoJSON upload plus Leaflet draw/edit controls to the Cases workspace**

- [ ] **Step 6: Run geospatial, case, migration, and UI tests**

- [ ] **Step 7: Commit**

```powershell
git add app/models/fra_geospatial_schemas.py app/services/fra_geospatial_import.py app/api/fra_geospatial_routes.py app/main.py app/static/fra requirements.txt tests
git commit -m "feat: import FRA geospatial evidence"
```

### Task 7: Cross-Layer Spatial Evaluation

**Files:**
- Create: `app/services/fra_reference_spatial.py`
- Modify: `app/services/fra_spatial_policy.py`
- Modify: `app/models/fra_schemas.py`
- Modify: `app/api/fra_routes.py`
- Modify: `app/static/fra/cases.js`
- Test: `tests/test_fra_reference_spatial.py`
- Modify: `tests/test_fra_spatial_policy.py`
- Modify: `tests/test_fra_api.py`
- Modify: `tests/fra_cases_ui.test.js`

**Interfaces:**
- Produces: `evaluate_reference_intersections(session, geometry, kinds, policy_version) -> list[ReferenceSpatialFinding]`.
- Extends spatial-evaluation response with `claim_findings` and `reference_findings` while retaining existing `findings` compatibility.

- [ ] **Step 1: Write failing tests for administrative containment, protected-area intersection, water intersection, cadastral overlap, version provenance, and non-blocking outcomes**

```python
result = evaluate_reference_intersections(session, candidate, {"protected_area"}, "fra-reference-v1")
assert result[0].reason == "intersects_protected_area"
assert result[0].outcome == "review_required"
assert result[0].reference_source_version == "tn-forest-2026"
```

- [ ] **Step 2: Verify failure and implement SQLite/Shapely and PostGIS query paths with consistent square-metre metrics**

- [ ] **Step 3: Write failing API/privacy tests and implement additive response serialization**

- [ ] **Step 4: Add an accessible findings table and reviewer disposition controls to Cases**

- [ ] **Step 5: Run spatial, API, case UI, and full tests**

- [ ] **Step 6: Commit**

```powershell
git add app/services/fra_reference_spatial.py app/services/fra_spatial_policy.py app/models/fra_schemas.py app/api/fra_routes.py app/static/fra/cases.js tests
git commit -m "feat: evaluate FRA claims against reference layers"
```

### Task 8: STAC Scene Search and Historical Processing Jobs

**Files:**
- Create: `app/models/fra_imagery_schemas.py`
- Create: `app/services/stac_imagery.py`
- Create: `app/services/historical_evidence.py`
- Create: `app/api/fra_evidence_routes.py`
- Modify: `app/services/fra_job_handlers.py`
- Modify: `app/services/storage.py`
- Modify: `app/config.py`
- Modify: `app/main.py`
- Test: `tests/test_stac_imagery.py`
- Test: `tests/test_historical_evidence.py`
- Test: `tests/test_fra_evidence_api.py`

**Interfaces:**
- Produces: `STACClient.search(geometry, date_range, collections, max_cloud) -> list[SceneCandidate]`.
- Produces: `request_historical_evidence(session, claim, target_years, actor_id, idempotency_key)` and job handler `historical_evidence`.
- Produces routes: `POST /api/fra/claims/{id}/historical-evidence` and `GET /api/fra/claims/{id}/historical-evidence`.

- [ ] **Step 1: Write failing STAC tests using a local fake HTTP transport for spatial/date/cloud queries, pagination, timeout, malformed metadata, and scene ranking**

```python
candidates = client.search(geometry, (date(2004, 1, 1), date(2006, 12, 31)), ["landsat-c2-l2"], 30)
assert candidates[0].scene_id == "least-cloud-nearest-date"
```

- [ ] **Step 2: Verify failure and implement endpoint allow-listing, bounded responses, timeouts, pagination, and private asset URI handling**

- [ ] **Step 3: Write failing orchestration tests for target-year provider selection, missing model, retriable provider failure, version mismatch, and no partial artifact writes**

- [ ] **Step 4: Implement scene persistence, processor adapter boundary, artifact checksums, and `insufficient_model` result**

- [ ] **Step 5: Write failing API permission/idempotency tests and implement routes/job registration**

- [ ] **Step 6: Run imagery, worker, storage, API, and full tests**

- [ ] **Step 7: Commit**

```powershell
git add app/models/fra_imagery_schemas.py app/services/stac_imagery.py app/services/historical_evidence.py app/api/fra_evidence_routes.py app/services/fra_job_handlers.py app/services/storage.py app/config.py app/main.py tests
git commit -m "feat: orchestrate historical FRA imagery evidence"
```

### Task 9: Historical Evidence Report and Review Workflow

**Files:**
- Modify: `app/services/fra_reports.py`
- Modify: `app/api/fra_planning_routes.py`
- Modify: `app/static/fra/cases.js`
- Modify: `app/static/fra/reports.js`
- Modify: `app/static/fra/index.html`
- Test: `tests/test_fra_reports.py`
- Test: `tests/test_fra_planning_api.py`
- Modify: `tests/fra_cases_ui.test.js`

**Interfaces:**
- Produces protected `GET /api/fra/reports/claims/{claim_id}/historical-evidence`.
- Consumes `ImagerySceneRecord`, `ImageryArtifact`, `SatelliteObservation`, geometry versions, and reviewer disposition.

- [ ] **Step 1: Write failing report tests for acquisition dates, providers, cloud/quality flags, model versions, geometry provenance, escaped content, private URI redaction, and neutral legal language**

- [ ] **Step 2: Verify failure and implement the printable report with no-store headers and optional approved preview images**

- [ ] **Step 3: Write failing UI tests and add request-status, review, and open-report actions to Cases/Reports**

- [ ] **Step 4: Run report, planning API, privacy, and UI tests**

- [ ] **Step 5: Commit**

```powershell
git add app/services/fra_reports.py app/api/fra_planning_routes.py app/static/fra tests
git commit -m "feat: add historical FRA evidence reports"
```

### Task 10: Versioned DSS Fact Builder and Scheme Catalogue

**Files:**
- Create: `app/services/dss_facts.py`
- Create: `app/services/scheme_catalog.py`
- Create: `app/models/fra_dss_schemas.py`
- Modify: `app/services/dss_engine.py`
- Modify: `app/api/fra_routes.py`
- Modify: `app/api/fra_planning_routes.py`
- Modify: `app/static/fra/planner.js`
- Modify: `app/static/fra/index.html`
- Create: `data/tn_scheme_catalog.sample.json`
- Test: `tests/test_dss_facts.py`
- Test: `tests/test_scheme_catalog.py`
- Modify: `tests/test_dss_engine.py`
- Modify: `tests/test_fra_planning_api.py`
- Modify: `tests/fra_workspace_ui.test.js`

**Interfaces:**
- Produces: `derive_facts(session, claim, derivation_version, actor_id, idempotency_key) -> DSSFactSnapshot`.
- Produces: `POST /api/fra/dss/derive-and-evaluate` and versioned scheme-catalog admin routes.
- Consumes verified titles/assets/reference values only; absence requires adequate observation coverage.

- [ ] **Step 1: Write failing fact tests for verified source selection, unknown versus absent, stale facts, source provenance, privacy, and idempotency**

```python
snapshot = derive_facts(session, claim, "tn-facts-v1", reviewer.id, "facts-1")
assert snapshot.facts_json["has_active_title"]["value"] is True
assert snapshot.facts_json["water_source_present"]["value"] == "unknown"
```

- [ ] **Step 2: Verify failure and implement the fact builder with explicit source snapshots**

- [ ] **Step 3: Write failing scheme-catalog validation/authority/effective-date tests and implement catalogue service/routes**

- [ ] **Step 4: Write failing derive-and-evaluate API tests and make the Planner UI call it for a selected claim**

- [ ] **Step 5: Keep supplied-facts evaluation for admin/tests, label bundled catalogue entries non-authoritative, and prevent missing rows from becoming false**

- [ ] **Step 6: Run DSS, planning, UI, and full tests**

- [ ] **Step 7: Commit**

```powershell
git add app/services/dss_facts.py app/services/scheme_catalog.py app/models/fra_dss_schemas.py app/services/dss_engine.py app/api app/static/fra data/tn_scheme_catalog.sample.json tests
git commit -m "feat: derive versioned FRA scheme facts"
```

### Task 11: Verifier and Planner Operational Dashboards

**Files:**
- Create: `app/services/fra_dashboards.py`
- Create: `app/api/fra_dashboard_routes.py`
- Create: `app/static/fra/dashboard.js`
- Modify: `app/main.py`
- Modify: `app/static/fra/index.html`
- Modify: `app/static/fra/app.js`
- Modify: `app/static/fra/styles.css`
- Test: `tests/test_fra_dashboards.py`
- Test: `tests/test_fra_dashboard_api.py`
- Test: `tests/fra_dashboard_ui.test.js`
- Modify: `tests/test_fra_workspace_ui.py`

**Interfaces:**
- Produces `GET /api/fra/dashboard/verifier` and `GET /api/fra/dashboard/planner` with district/block/village filters.
- Consumes intake, archive, cases, findings, evidence, jobs, titles, assets, recommendations, and referrals.

- [ ] **Step 1: Write failing aggregation tests for lifecycle/right totals, granted area, review queues, deficits, recommendations/referrals, missing inputs, and hierarchy filters**

- [ ] **Step 2: Verify failure and implement database-side aggregations with privacy-minimized rows**

- [ ] **Step 3: Write failing API role/filter/privacy tests and implement routes**

- [ ] **Step 4: Write failing UI tests and add role-aware verifier/planner tables with clear empty/loading/error states and links into Cases**

- [ ] **Step 5: Run dashboard, workspace, accessibility-structure, Node, and full tests**

- [ ] **Step 6: Commit**

```powershell
git add app/services/fra_dashboards.py app/api/fra_dashboard_routes.py app/static/fra app/main.py tests
git commit -m "feat: add FRA operational dashboards"
```

### Task 12: End-to-End Verification, Browser Testing, and Documentation

**Files:**
- Create: `tests/test_fra_operational_journey.py`
- Modify: `README.md`
- Modify: `docs/FRA_FOUNDATION.md`
- Modify: `docs/MODEL_ADAPTERS.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/PRIVACY_RETENTION.md`

**Interfaces:**
- Verifies the complete non-Atlas journey and documents deployment/configuration/data limitations.

- [ ] **Step 1: Write the failing journey test**

The test creates a non-synthetic upload, processes a deterministic approved local adapter, reviews/promotes the archive record, authors a geometry, evaluates reference intersections, records an imagery scene/artifact through a fake STAC service, derives facts, evaluates a rule, creates a referral, and opens verifier/planner summaries.

- [ ] **Step 2: Run the journey test and fix only integration defects exposed by it**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_operational_journey.py`

- [ ] **Step 3: Update documentation with exact configuration, worker, import, model, STAC, privacy, and recovery procedures; remove statements that the worker is manifest-only**

- [ ] **Step 4: Run complete automated verification**

Run: `.\venv\Scripts\python.exe -m pytest -q`

Run: `Get-ChildItem tests -Filter '*.test.js' | ForEach-Object { node --test $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }`

Run: `docker compose build api`

Run: `docker compose up -d --build`

Run: `docker compose exec -T api alembic current`

- [ ] **Step 5: Perform browser testing at desktop and narrow widths**

Verify login; archive upload; extraction status; intake triage; case detail; geometry authoring; spatial findings; historical evidence status/report; derive-and-evaluate; referral; verifier dashboard; planner dashboard; keyboard focus; empty/loading/error states; and that the existing Atlas has not gained the excluded satellite/thematic UI.

- [ ] **Step 6: Commit documentation and end-to-end coverage**

```powershell
git add tests/test_fra_operational_journey.py README.md docs
git commit -m "test: verify FRA operational journey"
```

- [ ] **Step 7: Confirm clean worktree and meaningful history**

Run: `git status --short`

Run: `git log --oneline --decorate -15`

Expected: no worktree output; distinct commits for each delivery slice.
