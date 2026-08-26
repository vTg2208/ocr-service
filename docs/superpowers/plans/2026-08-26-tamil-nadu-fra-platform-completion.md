# Tamil Nadu FRA Platform Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the four FRA problem-statement workflows for Tamil Nadu with a searchable archive, filterable WebGIS atlas, replaceable model pipeline, versioned asset review, explainable scheme referrals, printable reports, and one cohesive protected UI.

**Architecture:** Extend the existing FastAPI/SQLAlchemy modular monolith with state-profile, archive, job, model, atlas, asset, referral, and reporting modules. A database-backed worker calls typed provider adapters; persisted results drive Leaflet workspaces and never directly determine legal validity or benefit approval.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL/PostGIS, SQLite/Shapely, server-hosted HTML/CSS/JavaScript, Leaflet, pytest/unittest, Node test runner.

**Spec:** `docs/superpowers/specs/2026-08-26-fra-platform-completion-design.md`

## Global Constraints

- Preserve the existing `/land-mapping`, legacy `/api/claims`, and P0 `/api/fra/*` behavior.
- Support only the Tamil Nadu state profile in this phase; return `unsupported_state` for other profiles.
- Keep staff users, rights holders, archive source records, and model identities separate.
- Store documents, model artifacts, source URIs, and report exports privately.
- Automated observations are supporting evidence and never legal-validity decisions.
- DSS recommendations and referrals are advisory and never benefit sanctions.
- Synthetic fixtures must carry `synthetic: true` and visible warnings through persistence, API, UI, and reports.
- Mutations require authentication and audit; review requires reviewer/admin; model registration and state-reference import require admin.
- Use tests first, observe the expected failure, and commit each independently testable slice.
- Preserve the unrelated user modification in `docs/FRA_FOUNDATION.md`.

---

### Task 1: Tamil Nadu profile and completion persistence model

**Files:**
- Create: `app/services/state_profiles.py`
- Create: `app/db/fra_completion_models.py`
- Modify: `app/db/__init__.py`
- Modify: `migrations/env.py`
- Create: `migrations/versions/20260826_0004_fra_completion.py`
- Create: `tests/test_state_profiles.py`
- Create: `tests/test_fra_completion_models.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces `StateProfile`, `TamilNaduProfile`, `get_state_profile(code_or_name)` and `UnsupportedStateError`.
- Produces models `FRAImportBatch`, `FRAArchiveRecord`, `FRAExtractionRun`, `ProcessingJob`, `ModelVersion`, `InferenceRun`, `FRAVillageProfile`, `AssetFeature`, `DSSReferral`, and `ReportArtifact`.
- Later tasks use exact table/attribute names from these models.

- [ ] **Step 1: Write failing profile tests**

```python
def test_tamil_nadu_profile_normalizes_administration():
    profile = get_state_profile("tn")
    assert profile.code == "TN"
    assert profile.normalize_district("  Thanjavur ") == "Thanjavur"
    assert profile.hierarchy == ("state", "district", "block", "village")

def test_unsupported_state_is_explicit():
    with pytest.raises(UnsupportedStateError) as error:
        get_state_profile("Odisha")
    assert error.value.code == "unsupported_state"
```

- [ ] **Step 2: Run the profile tests and confirm the missing-module failure**

Run: `python -m pytest -q tests/test_state_profiles.py`

Expected: FAIL because `app.services.state_profiles` does not exist.

- [ ] **Step 3: Implement the immutable profile registry**

Define a frozen `StateProfile` dataclass with `code`, `name`, `hierarchy`, `languages`, `normalize_district`, `normalize_block`, and `normalize_village`. Register only `TamilNaduProfile(code="TN", name="Tamil Nadu", hierarchy=("state", "district", "block", "village"), languages=("ta", "en"))`. Normalize whitespace and title casing without inventing administrative aliases.

- [ ] **Step 4: Write failing persistence tests**

```python
def test_archive_retains_append_only_extraction_runs(session, document, staff):
    batch = FRAImportBatch(source_label="Synthetic TN pack", state_code="TN", created_by=staff.id)
    record = FRAArchiveRecord(batch=batch, document=document, legacy_reference="TN-FRA-1",
                              state_code="TN", review_state="needs_review", synthetic=True)
    record.extraction_runs.append(FRAExtractionRun(raw_text="Form A", standardized_json={},
                                  field_evidence_json={}, overall_confidence=0.8))
    session.add(record); session.commit()
    assert len(record.extraction_runs) == 1
    assert record.synthetic is True

def test_model_inference_and_asset_retain_version_provenance(session, admin, village):
    model = ModelVersion(task="asset_detection", name="tn-assets", version="0.1.0",
                         adapter_type="manifest", status="active", metrics_json={"status": "not_evaluated"},
                         configuration_json={}, label_map_json={}, registered_by=admin.id)
    run = InferenceRun(model_version=model, input_entity_type="village",
                       input_entity_id=village.id, state="completed", input_json={}, output_json={})
    asset = AssetFeature(village=village, asset_class="water_body", geometry=POINT,
                         source_type="model", inference_run=run, provenance_json={"synthetic": True},
                         verification_state="unverified")
    session.add(asset); session.commit()
    assert asset.inference_run.model_version.version == "0.1.0"
```

- [ ] **Step 5: Implement focused completion models and constraints**

Use UUID primary keys and timestamps from existing model utilities. Add uniqueness for `(created_by, idempotency_key)` on batches, `(batch_id, legacy_reference)` on archive records, `(task_type, entity_id, idempotency_key)` on jobs, `(task, name, version)` on models, `(state_code, district_code, block_code, village_code)` on villages, and `(recommendation_id)` on the first referral per recommendation/idempotency tuple. Use JSON for flexible evidence/provenance, explicit columns for archive/atlas filters, and the existing WGS84 multipolygon type for village/asset polygons. Store point geometry as GeoJSON JSON so SQLite and PostgreSQL share the contract.

- [ ] **Step 6: Import metadata and create migration `20260826_0004`**

Create only the ten completion tables in dependency order and drop them in reverse order. Extend migration tests to upgrade a fresh SQLite database through `20260826_0004` and assert a single head.

- [ ] **Step 7: Run focused and migration tests**

Run: `python -m pytest -q tests/test_state_profiles.py tests/test_fra_completion_models.py tests/test_migrations.py`

Expected: PASS.

- [ ] **Step 8: Commit the persistence slice**

```powershell
git add app/services/state_profiles.py app/db/fra_completion_models.py app/db/__init__.py migrations/env.py migrations/versions/20260826_0004_fra_completion.py tests/test_state_profiles.py tests/test_fra_completion_models.py tests/test_migrations.py
git commit -m "feat: add Tamil Nadu FRA completion model"
```

---

### Task 2: Model registry, adapters, and persistent job orchestration

**Files:**
- Create: `app/services/model_gateway.py`
- Create: `app/services/processing_jobs.py`
- Create: `app/services/fra_job_handlers.py`
- Create: `scripts/run_fra_jobs.py`
- Create: `tests/test_model_gateway.py`
- Create: `tests/test_processing_jobs.py`

**Interfaces:**
- Produces provider protocols `DocumentOCRProvider`, `FRAEntityExtractor`, `LandCoverClassifier`, and `AssetDetector`.
- Produces result dataclasses `OCRModelResult`, `EntityExtractionResult`, and `AssetDetectionResult`.
- Produces `register_model`, `activate_model`, `enqueue_job`, `claim_next_job`, `complete_job`, `fail_job`, and `run_one_job`.
- Task 3 consumes archive extraction handlers; Task 6 consumes asset inference handlers.

- [ ] **Step 1: Write failing gateway contract tests**

```python
def test_manifest_entity_adapter_emits_versioned_tamil_nadu_fields():
    result = ManifestFRAEntityExtractor("tn-manifest-v1").extract(
        "synthetic-1", {"village": "Kottur", "district": "Thanjavur", "right_type": "IFR"}
    )
    assert result.model_version == "tn-manifest-v1"
    assert result.fields["state"] == "Tamil Nadu"
    assert result.provenance["synthetic"] is True

def test_model_outputs_reject_legal_conclusion_keys():
    with pytest.raises(ModelOutputValidationError, match="legal conclusion"):
        validate_model_output({"approved": True})
```

- [ ] **Step 2: Run and confirm missing gateway failure**

Run: `python -m pytest -q tests/test_model_gateway.py`

- [ ] **Step 3: Implement typed protocols and deterministic adapters**

Validate task/adapter/version manifests, confidence in `[0,1]`, synthetic provenance, and banned keys `valid`, `invalid`, `approved`, `rejected`, `eligibility`, `sanctioned`. Provide deterministic manifest entity and asset adapters; wrap the existing satellite interfaces without changing them.

- [ ] **Step 4: Write failing job lifecycle tests**

```python
def test_worker_claims_a_job_once_and_completes_it(session, staff):
    job = enqueue_job(session, task_type="archive_extract", entity_type="archive_record",
                      entity_id=uuid.uuid4(), actor_id=staff.id, idempotency_key="extract-1", payload={})
    session.commit()
    claimed = claim_next_job(session, worker_id="worker-a")
    assert claimed.id == job.id and claimed.state == "running"
    complete_job(session, claimed, result={"run_id": "x"})
    assert claim_next_job(session, worker_id="worker-b") is None

def test_permanent_failure_is_quarantined_without_partial_result(session, job):
    fail_job(session, job, code="invalid_manifest", message="Bad labels", retriable=False)
    assert job.state == "quarantined"
    assert job.result_json == {}
```

- [ ] **Step 5: Implement atomic job operations and handler registry**

Use `SELECT ... FOR UPDATE SKIP LOCKED` for PostgreSQL and deterministic update/flush behavior for SQLite. Retry retriable failures until `max_attempts`; quarantine permanent failures. `run_one_job` loads a handler by task type, commits only after the handler result succeeds, rolls back partial domain rows on failure, then records the failure state in a fresh transaction.

- [ ] **Step 6: Add CLI worker**

`python -m scripts.run_fra_jobs --once` processes one eligible job and exits with JSON status. `--max-jobs N` processes at most N jobs. It never loops forever by default.

- [ ] **Step 7: Run focused tests and commit**

Run: `python -m pytest -q tests/test_model_gateway.py tests/test_processing_jobs.py tests/test_satellite_evidence.py`

```powershell
git add app/services/model_gateway.py app/services/processing_jobs.py app/services/fra_job_handlers.py scripts/run_fra_jobs.py tests/test_model_gateway.py tests/test_processing_jobs.py
git commit -m "feat: add pluggable FRA model jobs"
```

---

### Task 3: Searchable archive ingestion, extraction review, and promotion

**Files:**
- Create: `app/services/fra_archive.py`
- Create: `tests/test_fra_archive.py`

**Interfaces:**
- Produces `create_import_batch`, `create_archive_record`, `process_archive_extraction`, `review_archive_record`, `search_archive`, and `promote_archive_record`.
- Consumes Task 1 models/profiles, Task 2 model/job services, existing private storage/document records, and P0 `create_claim`/`add_geometry_version`.

- [ ] **Step 1: Write failing archive service tests**

```python
def test_tamil_nadu_archive_record_is_searchable_after_review(session, document, staff, reviewer):
    batch = create_import_batch(session, source_label="TN synthetic", state="Tamil Nadu",
                                actor_id=staff.id, idempotency_key="batch-1", synthetic=True)
    record = create_archive_record(session, batch=batch, document_id=document.id,
                                   legacy_reference="TN-2008-1", actor_id=staff.id)
    process_archive_extraction(session, record, extractor=MANIFEST_EXTRACTOR,
                               manifest={"holder_name": "Ramu", "district": "Thanjavur",
                                         "block": "Kumbakonam", "village": "Kottur",
                                         "right_type": "IFR", "claim_status": "submitted"}, actor_id=staff.id)
    review_archive_record(session, record, reviewed_fields=record.latest_extraction.standardized_json,
                          reviewer_id=reviewer.id, expected_revision=0)
    assert search_archive(session, query="Ramu Kottur", filters={"district": "Thanjavur"})[0].id == record.id

def test_stale_review_and_unsupported_state_do_not_mutate_record(session, record, reviewer):
    with pytest.raises(ArchiveConflictError, match="changed since"):
        review_archive_record(session, record, reviewed_fields={}, reviewer_id=reviewer.id,
                              expected_revision=99)
    assert record.review_state == "needs_review"
```

- [ ] **Step 2: Run and confirm missing archive service**

Run: `python -m pytest -q tests/test_fra_archive.py`

- [ ] **Step 3: Implement batch/record idempotency and extraction versioning**

Require `TN`, private `Document`, non-empty legacy reference, source provenance, and synthetic flag agreement between batch and record. Compute a duplicate fingerprint from normalized state/reference/document hash. Extraction adds a run and changes the record to `needs_review`; it never overwrites earlier output.

- [ ] **Step 4: Implement reviewed fields and search**

Validate required fields `holder_name`, `district`, `block`, `village`, `right_type`, and `claim_status`. Increment `revision`, persist reviewer/time and explicit columns. PostgreSQL uses a weighted full-text expression; SQLite uses escaped case-insensitive `LIKE` across the explicit search fields. Apply filters with AND semantics and stable pagination/order.

- [ ] **Step 5: Implement promotion**

Only `reviewed` records can promote. Create/find rights holder and Gram Sabha from reviewed fields, call the P0 FRA claim service, attach the archive document/provenance, and set `promoted_claim_id` atomically. Repeated promotion returns the same claim.

- [ ] **Step 6: Run service tests and commit**

Run: `python -m pytest -q tests/test_fra_archive.py tests/test_fra_claims.py tests/test_claim_service.py`

```powershell
git add app/services/fra_archive.py tests/test_fra_archive.py
git commit -m "feat: add searchable Tamil Nadu FRA archive"
```

---

### Task 4: Archive, job, and model APIs

**Files:**
- Create: `app/models/fra_completion_schemas.py`
- Create: `app/api/fra_archive_routes.py`
- Create: `app/api/fra_operations_routes.py`
- Modify: `app/main.py`
- Create: `tests/test_fra_archive_api.py`
- Create: `tests/test_fra_operations_api.py`

**Interfaces:**
- Exposes archive/model/job routes from the spec.
- Produces privacy-safe serializers shared by the workspace UI.

- [ ] **Step 1: Write failing protected-route tests**

```python
def test_archive_routes_reject_anonymous_and_unsupported_state(client, staff_headers):
    assert client.post("/api/fra/archive/batches", json={}).status_code == 401
    response = client.post("/api/fra/archive/batches", headers=staff_headers,
                           json={"source_label": "x", "state": "Odisha",
                                 "idempotency_key": "b1", "synthetic": True})
    assert response.status_code == 422
    assert response.json()["message"]["code"] == "unsupported_state"

def test_normal_user_cannot_register_or_activate_models(client, staff_headers, admin_headers):
    payload = {"task": "entity_extraction", "name": "tn-ner", "version": "0.1.0",
               "adapter_type": "manifest", "metrics": {"status": "not_evaluated"}}
    assert client.post("/api/fra/models", headers=staff_headers, json=payload).status_code == 403
    assert client.post("/api/fra/models", headers=admin_headers, json=payload).status_code == 201
```

- [ ] **Step 2: Run and observe 404 failures**

Run: `python -m pytest -q tests/test_fra_archive_api.py tests/test_fra_operations_api.py`

- [ ] **Step 3: Implement strict Pydantic contracts and route orchestration**

Accept JSON metadata plus an existing private `document_id` for archive records; reuse `/api/pattas/process` for secure file upload rather than duplicate upload validation. Map unsupported state/invalid manifest to 422, duplicate/stale review to 409, unavailable model to 503, and missing resources to 404. Route boundaries audit only mutations not already audited by services.

- [ ] **Step 4: Implement authorization and privacy tests**

Cover batch/record creation, list/search/filter/pagination, reviewer correction, promotion, job listing/retry, model registration/activation, idempotency, and omission of raw text/private URIs from list responses.

- [ ] **Step 5: Run API and legacy regressions, then commit**

Run: `python -m pytest -q tests/test_fra_archive_api.py tests/test_fra_operations_api.py tests/test_fra_api.py tests/test_land_api.py`

```powershell
git add app/models/fra_completion_schemas.py app/api/fra_archive_routes.py app/api/fra_operations_routes.py app/main.py tests/test_fra_archive_api.py tests/test_fra_operations_api.py
git commit -m "feat: expose FRA archive and model operations"
```

---

### Task 5: Tamil Nadu village reference data and FRA Atlas APIs

**Files:**
- Create: `app/services/fra_atlas.py`
- Create: `app/api/fra_atlas_routes.py`
- Create: `data/synthetic_tamil_nadu_fra_atlas.geojson`
- Create: `scripts/import_fra_villages.py`
- Modify: `app/main.py`
- Create: `tests/test_fra_atlas.py`
- Create: `tests/test_fra_atlas_api.py`

**Interfaces:**
- Produces `import_village_profiles`, `atlas_features`, `atlas_summary`, `list_villages`, and `village_detail`.
- Exposes `/api/fra/atlas/features`, `/api/fra/atlas/summary`, and village routes.

- [ ] **Step 1: Write failing atlas import/query tests**

```python
def test_imported_tamil_nadu_villages_keep_synthetic_provenance(session, admin, atlas_payload):
    report = import_village_profiles(session, atlas_payload, actor_id=admin.id)
    village = session.scalar(select(FRAVillageProfile))
    assert report.inserted == 3
    assert village.state_code == "TN"
    assert village.provenance_json["synthetic"] is True

def test_atlas_filters_and_summary_use_same_scope(session, seeded_atlas):
    filters = AtlasFilters(district="Thanjavur", right_type="IFR", status="granted")
    features = atlas_features(session, filters, privileged=False)
    summary = atlas_summary(session, filters)
    assert summary.claim_count == len([f for f in features if f["properties"]["kind"] == "claim"])
    assert "rights_holder_id" not in json.dumps(features)
```

- [ ] **Step 2: Implement strict synthetic GeoJSON import**

Require `metadata.state_code == "TN"`, `synthetic == true`, version/source, unique district/block/village codes, valid polygonal WGS84 geometry, and Tamil Nadu names. Upsert idempotently and audit the import summary.

- [ ] **Step 3: Implement shared Atlas filters and aggregates**

Use one `AtlasFilters` dataclass for state, district, block, village, tribal group, right type, status, year, and layer list. Return a GeoJSON FeatureCollection with `kind` values `village`, `claim`, `title`, and `asset`. Normal users receive claim number/right/status only; reviewers/admins may receive internal related IDs. Summary returns counts and square metres grouped by right type/status and administrative level.

- [ ] **Step 4: Implement API and CLI tests**

Cover authentication, unsupported state, filter combinations, empty state, privacy, invalid layer, deterministic order, matching summary scope, and idempotent import CLI output.

- [ ] **Step 5: Run and commit**

Run: `python -m pytest -q tests/test_fra_atlas.py tests/test_fra_atlas_api.py tests/test_fra_spatial_policy.py`

```powershell
git add app/services/fra_atlas.py app/api/fra_atlas_routes.py app/main.py data/synthetic_tamil_nadu_fra_atlas.geojson scripts/import_fra_villages.py tests/test_fra_atlas.py tests/test_fra_atlas_api.py
git commit -m "feat: add Tamil Nadu FRA Atlas APIs"
```

---

### Task 6: Asset inference, review, and supersession

**Files:**
- Create: `app/services/fra_assets.py`
- Create: `app/api/fra_asset_routes.py`
- Modify: `app/models/fra_completion_schemas.py`
- Modify: `app/main.py`
- Create: `data/synthetic_tamil_nadu_asset_manifest.json`
- Create: `tests/test_fra_assets.py`
- Create: `tests/test_fra_asset_api.py`

**Interfaces:**
- Produces `enqueue_asset_inference`, `process_asset_inference`, `list_assets`, and `review_asset`.
- Exposes `/api/fra/assets/inference-jobs`, `/api/fra/assets`, and `/api/fra/assets/{asset_id}/review`.

- [ ] **Step 1: Write failing asset lifecycle tests**

```python
def test_manifest_inference_creates_unverified_supporting_assets(session, village, model, staff):
    job = enqueue_asset_inference(session, village_id=village.id, claim_id=None,
                                  model_version_id=model.id, scene_id="tn-scene-2005",
                                  actor_id=staff.id, idempotency_key="asset-1")
    assets = process_asset_inference(session, job, adapter=TN_ASSET_ADAPTER)
    assert {a.asset_class for a in assets} == {"forest_cover", "water_body"}
    assert all(a.verification_state == "unverified" for a in assets)
    assert all(a.provenance_json["synthetic"] for a in assets)

def test_reviewer_correction_supersedes_without_overwriting_model_output(session, asset, reviewer):
    corrected = review_asset(session, asset, outcome="corrected", reviewer_id=reviewer.id,
                             corrected_value={"present": False}, reasons=["Field visit TN-1"])
    assert corrected.supersedes_id == asset.id
    assert asset.verification_state == "superseded"
```

- [ ] **Step 2: Implement inference transaction and legal-output guard**

Require an active compatible model, Tamil Nadu village/claim geometry, registered scene, allowed asset classes, confidence, synthetic marker, and no banned legal/DSS conclusion keys. Store `InferenceRun` plus all unverified `AssetFeature` rows atomically and link satellite evidence when claim-scoped.

- [ ] **Step 3: Implement reviewer verification/rejection/correction**

Require reviewer/admin, non-empty reasons for rejection/correction, optimistic revision, immutable original output, and audit. A correction creates a manual-source superseding feature.

- [ ] **Step 4: Implement APIs and focused tests**

Cover authentication, provider/model unavailable 503, invalid outputs 422 with rollback, idempotent jobs, asset filters, source URI privacy, reviewer authorization, and response warnings.

- [ ] **Step 5: Run and commit**

Run: `python -m pytest -q tests/test_fra_assets.py tests/test_fra_asset_api.py tests/test_model_gateway.py tests/test_satellite_evidence.py`

```powershell
git add app/services/fra_assets.py app/api/fra_asset_routes.py app/models/fra_completion_schemas.py app/main.py data/synthetic_tamil_nadu_asset_manifest.json tests/test_fra_assets.py tests/test_fra_asset_api.py
git commit -m "feat: add versioned FRA asset review"
```

---

### Task 7: DSS planning referrals and printable reports

**Files:**
- Create: `app/services/dss_referrals.py`
- Create: `app/services/fra_reports.py`
- Create: `app/api/fra_planning_routes.py`
- Modify: `app/models/fra_completion_schemas.py`
- Modify: `app/main.py`
- Create: `tests/test_dss_referrals.py`
- Create: `tests/test_fra_reports.py`
- Create: `tests/test_fra_planning_api.py`

**Interfaces:**
- Produces `list_recommendations`, `create_referral`, `update_referral`, `render_archive_report`, `render_claim_report`, and `render_village_report`.
- Exposes the DSS list/referral and HTML report routes from the spec.

- [ ] **Step 1: Write failing referral tests**

```python
def test_planner_referral_is_advisory_and_retains_history(session, recommendation, reviewer):
    referral = create_referral(session, recommendation_id=recommendation.id,
                               department="Rural Development", priority="high",
                               actor_id=reviewer.id, idempotency_key="ref-1")
    update_referral(session, referral, status="under_review", notes="Assigned locally",
                    actor_id=reviewer.id, expected_revision=0)
    assert referral.history_json[-1]["status"] == "under_review"
    assert referral.advisory_only is True
```

- [ ] **Step 2: Implement referral state machine**

Allow `draft -> referred -> under_review -> closed` and `draft/referred/under_review -> withdrawn`. Require notes for closed/withdrawn, optimistic revision, append-only history, one audit per mutation, and no status named approved/sanctioned/eligible.

- [ ] **Step 3: Write failing report safety tests**

```python
def test_village_report_has_provenance_and_mandatory_warnings(session, village, planner):
    html = render_village_report(session, village.id, actor_id=planner.id)
    assert "Synthetic demonstration data" in html
    assert "supporting evidence and do not determine legal validity" in html
    assert "advisory and do not approve or sanction benefits" in html
    assert "private://" not in html
```

- [ ] **Step 4: Implement escaped printable HTML reports**

Use `html.escape` for all variable content, semantic headings/tables, print CSS, private/no-store headers, filters/provenance timestamps, and the exact mandatory warnings. Archive reports require reviewer/admin because they include raw extraction text; claim/village reports use privacy-safe serializers for normal users.

- [ ] **Step 5: Implement planning APIs and tests**

Cover grouped recommendation filters, missing inputs, idempotent referral creation, authorization, invalid transitions 409, stale updates 409, report authentication/privacy/content type/cache headers, and audit events.

- [ ] **Step 6: Run and commit**

Run: `python -m pytest -q tests/test_dss_referrals.py tests/test_fra_reports.py tests/test_fra_planning_api.py tests/test_dss_engine.py`

```powershell
git add app/services/dss_referrals.py app/services/fra_reports.py app/api/fra_planning_routes.py app/models/fra_completion_schemas.py app/main.py tests/test_dss_referrals.py tests/test_fra_reports.py tests/test_fra_planning_api.py
git commit -m "feat: add FRA planning referrals and reports"
```

---

### Task 8: Protected workflow workspace shell and Archive UI

**Files:**
- Create: `app/static/fra/index.html`
- Create: `app/static/fra/styles.css`
- Create: `app/static/fra/api.js`
- Create: `app/static/fra/app.js`
- Create: `app/static/fra/archive.js`
- Modify: `app/main.py`
- Modify: `app/static/land-mapping/index.html`
- Create: `tests/fra_workspace_ui.test.js`
- Create: `tests/test_fra_workspace_ui.py`

**Interfaces:**
- Serves protected workflow shell at `/fra`.
- Produces browser modules `FRAApi`, `FRAWorkspace`, and `FRAArchiveUI` with CommonJS exports for Node tests.

- [ ] **Step 1: Write failing shell/browser logic tests**

```javascript
test('workspace preserves Tamil Nadu context between sections', () => {
  const state = FRAWorkspace.reduce(FRAWorkspace.initialState(),
    {type: 'context', value: {district: 'Thanjavur', village: 'Kottur'}});
  const atlas = FRAWorkspace.reduce(state, {type: 'section', value: 'atlas'});
  assert.equal(atlas.context.village, 'Kottur');
});

test('archive empty state distinguishes no records from no search matches', () => {
  assert.match(FRAArchiveUI.emptyState([], ''), /No archive records/);
  assert.match(FRAArchiveUI.emptyState([{id:'1'}], 'ramu'), /No matching records/);
});
```

- [ ] **Step 2: Implement protected route and semantic shell**

Add `/fra` `FileResponse`, session check/redirect, skip link, banner, persistent navigation for Archive/Atlas/Assets/DSS Planner/Reports, Tamil Nadu context bar, main live region, and signed-in identity/logout. Add a normal link from `/land-mapping`; do not replace its workflow.

- [ ] **Step 3: Implement Archive queue and review interface**

Render filter/search controls, status/count, record list, source/extraction review panes, confidence and provenance, reviewer correction form, processing/retry state, and promotion action. Use text nodes/escaped templates, request aborting, loading/empty/error states, and no raw private URI.

- [ ] **Step 4: Implement responsive institutional styling**

Reuse the existing AranyaSetu emblem, `Literata`/`Public Sans` typography, green-tinted tokens, square editorial geometry, visible `:focus-visible`, 44px targets, reduced motion, desktop side rail, and narrow stacked navigation. Do not add dark mode, gradients, glass effects, or generic metric cards.

- [ ] **Step 5: Run Node, static, and browser-route tests; commit**

Run: `node --test tests/fra_workspace_ui.test.js tests/land_mapping_ui.test.js`

Run: `python -m pytest -q tests/test_fra_workspace_ui.py tests/test_land_mapping_ui.py tests/test_demo_auth.py`

```powershell
git add app/static/fra app/main.py app/static/land-mapping/index.html tests/fra_workspace_ui.test.js tests/test_fra_workspace_ui.py
git commit -m "feat: add FRA archive workspace"
```

---

### Task 9: Atlas, Assets, Planner, and Reports UI

**Files:**
- Create: `app/static/fra/atlas.js`
- Create: `app/static/fra/assets.js`
- Create: `app/static/fra/planner.js`
- Create: `app/static/fra/reports.js`
- Modify: `app/static/fra/index.html`
- Modify: `app/static/fra/app.js`
- Modify: `app/static/fra/styles.css`
- Modify: `tests/fra_workspace_ui.test.js`
- Modify: `tests/test_fra_workspace_ui.py`

**Interfaces:**
- Adds all remaining visual workspaces to the shell from Task 8.
- Consumes privacy-safe APIs from Tasks 5–7.

- [ ] **Step 1: Write failing view-model tests**

```javascript
test('atlas query uses the same filters for features and summary', () => {
  const filters = {district:'Thanjavur', right_type:'IFR', status:'granted'};
  assert.equal(FRAAtlasUI.query(filters), 'district=Thanjavur&right_type=IFR&status=granted');
});

test('asset and DSS copy never presents automation as a decision', () => {
  assert.match(FRAAssetsUI.legalRole(), /supporting evidence/i);
  assert.match(FRAPlannerUI.disclaimer(), /does not approve or sanction/i);
});
```

- [ ] **Step 2: Implement Atlas workspace**

Initialize Leaflet only when the Atlas is visible. Render Tamil Nadu administrative filters, layer checkboxes, synchronized features/summary, accessible non-map result list, legend, selected feature drawer, and no-data/error states. Clear and replace layers on every filter change; never append stale results.

- [ ] **Step 3: Implement Assets workspace**

Render model/job selector, queued/running/failure state, time-ordered assets, map overlay, confidence/provenance, verification state, and reviewer actions. Synthetic/model warnings remain visible and legal conclusion words are absent.

- [ ] **Step 4: Implement Planner and Reports workspaces**

Render recommendation filters and grouped reasons/missing facts, referral creation/history, advisory copy, and links to printable reports. Reports open in a new authenticated tab and state that browser Print/Save as PDF is the export path.

- [ ] **Step 5: Run UI tests and commit**

Run: `node --test tests/fra_workspace_ui.test.js tests/land_mapping_ui.test.js`

Run: `python -m pytest -q tests/test_fra_workspace_ui.py tests/test_fra_atlas_api.py tests/test_fra_asset_api.py tests/test_fra_planning_api.py`

```powershell
git add app/static/fra tests/fra_workspace_ui.test.js tests/test_fra_workspace_ui.py
git commit -m "feat: complete FRA workflow workspaces"
```

---

### Task 10: Tamil Nadu demonstration pack, model attachment guide, and evaluation harness

**Files:**
- Create: `data/synthetic_tamil_nadu_fra_archive.json`
- Create: `scripts/seed_tamil_nadu_fra_demo.py`
- Create: `scripts/evaluate_fra_models.py`
- Create: `docs/MODEL_ADAPTERS.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/PRIVACY_RETENTION.md`
- Modify: `README.md`
- Create: `tests/test_tamil_nadu_demo.py`
- Create: `tests/test_fra_model_evaluation.py`

**Interfaces:**
- Produces idempotent demo command `python -m scripts.seed_tamil_nadu_fra_demo`.
- Produces evaluation command `python -m scripts.evaluate_fra_models --predictions FILE --labels FILE --task TASK`.
- Documents how a trained model implements and registers each gateway contract.

- [ ] **Step 1: Write failing seed/evaluation tests**

```python
def test_tamil_nadu_seed_is_idempotent_and_all_records_are_synthetic(session):
    first = seed_demo(session, actor_id=ADMIN_ID)
    second = seed_demo(session, actor_id=ADMIN_ID)
    assert first.created > 0 and second.created == 0
    assert all(row.synthetic for row in session.scalars(select(FRAArchiveRecord)))

def test_unevaluated_model_is_not_given_fake_accuracy():
    report = evaluation_report([], [], task="asset_detection")
    assert report == {"task": "asset_detection", "status": "not_evaluated", "sample_count": 0}
```

- [ ] **Step 2: Implement a coherent synthetic Tamil Nadu story**

Seed three villages, at least one IFR/CR/CFR archive record, reviewed/pending states, claims/geometries/titles, time-separated scenes, verified/unverified assets, demo rules/recommendations/referrals, and provenance. Use invented names and coordinates inside the visibly synthetic reference boundary pack; never use contact details from the problem statement as case data.

- [ ] **Step 3: Implement evaluation schemas**

For `ocr`, compute CER/WER using existing evaluation utilities. For `entity_extraction`, compute per-label and macro precision/recall/F1. For `asset_classification`, compute per-class precision/recall/F1 and accept optional IoU values supplied by a future segmentation adapter. Empty labels return `not_evaluated`, never `1.0`.

- [ ] **Step 4: Document model attachment and operational boundaries**

Give concrete manifest examples for local Python, artifact, and REST adapters; registration/activation commands; worker execution; seed/import commands; evaluation inputs; UI routes; Tamil Nadu-first limitations; and exact supporting/advisory warnings. Explain that authoritative reference data and legally approved scheme rules must replace synthetic records before operational use.

- [ ] **Step 5: Run focused tests and commit**

Run: `python -m pytest -q tests/test_tamil_nadu_demo.py tests/test_fra_model_evaluation.py`

```powershell
git add data/synthetic_tamil_nadu_fra_archive.json scripts/seed_tamil_nadu_fra_demo.py scripts/evaluate_fra_models.py docs/MODEL_ADAPTERS.md docs/OPERATIONS.md docs/PRIVACY_RETENTION.md README.md tests/test_tamil_nadu_demo.py tests/test_fra_model_evaluation.py
git commit -m "docs: complete Tamil Nadu FRA demonstration"
```

---

### Task 11: Full verification and browser acceptance

**Files:**
- Modify only files required by defects proven during this task.

**Interfaces:**
- Verifies every completion criterion in the spec and leaves a clean feature branch except for the preserved user-owned newline change if it remains.

- [ ] **Step 1: Run the complete automated suite from a fresh process**

```powershell
python -m pytest -q
node --test tests/land_mapping_ui.test.js tests/fra_workspace_ui.test.js
python -m compileall -q app scripts
python -m alembic heads
docker compose config --quiet
git diff --check
```

Expected: zero failures, a single Alembic head `20260826_0004`, and no whitespace errors in implementation files.

- [ ] **Step 2: Seed a fresh temporary database and run the worker**

```powershell
python -m alembic upgrade head
python -m scripts.seed_tamil_nadu_fra_demo
python -m scripts.run_fra_jobs --max-jobs 20
```

Expected: migrations succeed; first seed creates records; a repeated seed creates none; queued demo jobs complete or report an explicit unavailable-model state without partial rows.

- [ ] **Step 3: Perform Chrome browser acceptance**

Exercise `/login -> /fra -> Archive -> Atlas -> Assets -> DSS Planner -> Reports -> logout` at desktop and 390x844. Verify page identity, meaningful DOM, no framework overlay, no relevant console errors, screenshots, keyboard-visible focus, interaction state, no horizontal overflow, Tamil Nadu filters, synthetic warnings, supporting/advisory warnings, and privacy-safe output.

- [ ] **Step 4: Review spec requirement by requirement**

Record evidence for archive, model attachment, jobs, Atlas, assets, referrals, reports, Tamil Nadu profile, safety, privacy, accessibility, evaluation, seed data, and regressions. Any unavailable external model/data remains explicitly labelled rather than represented as implemented.

- [ ] **Step 5: Commit only proven verification fixes**

If verification changed code, commit each coherent fix with its regression test. If no files changed, do not create an empty commit.
