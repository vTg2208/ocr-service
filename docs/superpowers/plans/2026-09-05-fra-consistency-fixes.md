# FRA Consistency Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Complete the existing access, review, promotion, and cross-module data contracts authorized in steps 1 and 2.

**Architecture:** Reuse the native case access policy across route generations, consolidate public promotion through the intake service, and enforce concurrent mutations at the database boundary. Keep common taxonomy/location contracts in small shared modules and test through real producers and consumers.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite tests/PostGIS deployment, Pydantic, vanilla JavaScript, pytest, Node test runner.

**Spec:** docs/superpowers/specs/2026-09-05-fra-consistency-fixes.md

## Global Constraints

- Scope is the existing Tamil Nadu implementation. No new ML models, real scheme eligibility definitions, frameworks, or national rollout.
- Retain provenance and history; do not overwrite stored recommendation/fact/extraction evidence to repair contracts.
- Existing test DBs only. Do not touch the user's local database, uploads, secrets, untracked icon, analysis document, or problem-statement file.
- Keep dependencies unchanged unless a demonstrated failure makes a change necessary.
- Use the existing venv at C:/Users/vvthe/Downloads/projects/ocr-service/venv/Scripts/python.exe.

### Task 1: Consistent case authorization and reviewed promotion

**Files:** app/api/fra_routes.py, fra_case_routes.py, fra_asset_routes.py, fra_planning_routes.py, fra_operations_routes.py, fra_evidence_routes.py, fra_intake_routes.py; app/services/fra_intake.py, fra_cases.py; app/models/fra_schemas.py; tests/test_fra_api.py, test_fra_case_api.py, test_fra_intake_api.py, test_fra_asset_api.py, test_fra_planning_api.py, test_fra_operations_api.py. A focused app/api/fra_access.py helper may centralize existing policy.

**Interfaces:** Consume can_view_case(claim, user_id, privileged). Preserve owner/reviewer policy, 404 hiding for inaccessible records, 403 for reviewer-only operations. Public legacy promotion must consume promote_intake(... expected_revision=...) rather than promote_legacy_claim directly. Add required expected_revision to LegacyPromotionCreate and return the existing foundation claim shape; expose intake state/link/revision as appropriate without breaking successful existing response fields.

- [x] Add cross-owner tests covering foundation detail, geometry/evidence writes, satellite/spatial operations, related recommendation/asset/report access, and document attachment. The existing test fixture creates two users and two cases; assert denied responses and unchanged row/audit counts, plus successful owner/reviewer controls.

```python
hidden = client.get(f'/api/fra/claims/{other_case_id}', headers=owner_headers)
assert hidden.status_code == 404
denied = client.post(f'/api/fra/claims/{other_case_id}/evidence', headers=owner_headers,
                     json={'category': 'documentary', 'source': 'test', 'description': 'Source record'})
assert denied.status_code == 404
```

- [x] Add promotion tests: normal user 403; reviewer awaiting_triage 409; stale expected_revision 409; reviewed promotion creates exactly one native case, marks intake promoted, and sets promoted_claim_id. Repeat through both routes with current revision returns the same case. Mismatched repeat parameters must not falsely claim a new promotion succeeded. Missing intake must not silently bypass triage.
- [x] Run the new tests and record expected baseline failures.
- [x] Apply existing visibility checks to every relevant case-bound route before reads, mutations, or expensive work. Scope list queries before serialization. Check attached Document ownership for ordinary users. Preserve explicitly shared Atlas/reference surfaces.
- [x] Route the compatibility promotion endpoint through reviewed intake. Reuse the shared service and existing errors, require reviewer role and expected_revision, and preserve idempotent successful behavior.
- [x] Run targeted API tests and update old tests that deliberately encoded the superseded unreviewed promotion contract. Commit code and tests with a focused message. Report files, tests, assumptions, and residual concerns.

### Task 2: Atomic review and lifecycle mutations

**Files:** app/services/fra_intake.py, fra_archive.py, fra_assets.py, dss_referrals.py, historical_evidence.py, fra_workflow.py, fra_claims.py; related API exception handling and tests/test_fra_intake.py, test_fra_archive.py, test_fra_assets.py, test_dss_referrals.py, test_historical_evidence.py, test_fra_workflow.py, test_fra_claims.py. A shared app/services/concurrency.py helper is permitted.

**Interfaces:** Preserve existing expected_revision contracts and conflict exception types. Use database conditional updates or real optimistic version checks so a stale session cannot write a second valid review. Preserve the transaction containing domain changes and audit rows. Guard status-based decisions and title/geometry allocation against concurrent stale state without adding new legal requirements.

- [x] Add two-session tests: both sessions load revision N, first commits a review, second attempts a different review with N and must fail without persisting audit or promotion/correction side effects. Add equivalent stale lifecycle/title coverage using the existing model fields.

```python
first = session_one.get(Model, record_id)
stale = session_two.get(Model, record_id)
review(session_one, first, expected_revision=0, **first_change)
session_one.commit()
with pytest.raises(ConflictError):
    review(session_two, stale, expected_revision=0, **second_change)
session_two.rollback()
```

- [x] Run the new tests to establish failures.
- [x] Implement database-enforced stale-write rejection, preserving route-level 409 and explicit rollback. Validate reasons/state before mutations; no event or history row may survive a failed update. Ensure reviewer-only operations retain role gates established by Task 1.
- [x] Test duplicate promotion/title/geometry allocation and transactional failure. Run targeted service/API suites and commit the complete change.

### Task 3: Producer-consumer contracts and consistent spatial summaries

**Files:** app/services/model_gateway.py, fra_assets.py, dss_facts.py, dss_engine.py, fra_geospatial_import.py, fra_cases.py, fra_atlas.py, fra_dashboards.py; data/demo_dss_rules.json; scripts/seed_tamil_nadu_fra_demo.py; app/static/fra/{app,atlas,assets,cases,dashboard,planner}.js; related API schemas and tests. Add focused shared taxonomy/location helpers as needed.

**Interfaces:** Canonical asset classes remain the supported model classes, with explicit backward-compatible aliases. Current derived fact names include has_active_title, agricultural_observation, forest_observation, water_source_present, homestead_observation. Add a distinct numeric agricultural cover fact where an explicit finite fraction in [0,1] exists; missing measurement stays unknown. Version changed sample rules/fact contracts, preserving prior persisted outputs. Canonical water-stress reference kinds must be accepted through import before DSS consumption.

- [x] Add an integration test that produces/reviews agricultural_cover and water assets through the actual asset service/API, derives facts, and evaluates the sample rules. Assert exact expected values and unknown for presence without a measured fraction. Test historical aliases, stale/unverified observations, and rejection of invalid numeric fractions.
- [x] Add import-to-fact coverage for published water-stress data; add cases with direct Gram Sabha, holder Gram Sabha, and parcel-only locations. Assert matching case/Atlas/dashboard membership and counts. Test totals beyond queue limits and failed archive jobs/village assets.
- [x] Add JavaScript behavioral tests for context changes in hidden and visible panels, subsequent activation, and filter-preserving reloads; verify the existing context controls affect planner recommendations as well as maps.
- [x] Observe failures; implement focused common contracts and location resolution. Update sample seeding to exercise derived facts and versioned rules rather than supplying an incompatible parallel dictionary. Preserve historical rows and synthetic labeling.
- [x] Ensure API filters, browser context, feature layers, recommendation lists, and dashboard aggregates consume the same definitions. Keep numeric facts distinct from booleans and preserve evidence provenance and unknown semantics.
- [x] Run targeted suites, then all pytest and four Node suites. Commit changes and report exact evidence.

### Completion review

- [x] Review each task diff for spec compliance and correctness; fix findings and re-review changed code. Task 1 had an independent review; subsequent reviews ran inline at the user's request.
- [x] Run complete Python/Node suites once all changes are integrated. Check git diff whitespace and document compatibility changes and verification limits.
- [x] Integrate the completed changes into the user's checkout without changing pre-existing untracked files. Leave a clear final summary of behavior, tests, and material limitations.
