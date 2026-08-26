# FRA Platform P0 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backward-compatible FRA domain, right-aware spatial policy, supporting satellite-evidence providers, and a versioned explainable DSS foundation.

**Architecture:** Keep the legacy `Claim` workflow unchanged and add a protected `/api/fra/*` domain. Focused SQLAlchemy models, service modules, and provider interfaces share the existing database, identity, audit, document, parcel, and geometry infrastructure. Every new behavior is introduced through a red-green-refactor cycle.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL/PostGIS, SQLite/Shapely, pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-08-26-fra-platform-p0-foundation-design.md`

## Global Constraints

- Preserve the existing `Claim`, `/api/claims`, and browser behavior.
- Expose new behavior only under `/api/fra/*`.
- Keep staff `User` records separate from FRA rights holders.
- Satellite output is supporting evidence and never a legal-validity decision.
- DSS output is advisory and never an automatic benefit sanction.
- Do not add real imagery, trained models, OIDC, or government integrations.
- Mutations require authentication and audit events; reviewer decisions require `reviewer` or `admin`; rule creation requires `admin`.
- Use tests first and observe the expected failure before production changes.
- Preserve all existing user changes and do not rewrite unrelated modules.

---

### Task 1: FRA persistence model and migration

**Files:**
- Create: `app/db/fra_models.py`
- Modify: `app/db/__init__.py`
- Modify: `migrations/env.py`
- Create: `migrations/versions/20260826_0003_fra_foundation.py`
- Create: `tests/test_fra_database_models.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces SQLAlchemy models: `RightsHolder`, `GramSabha`, `FRAClaim`, `FRADecision`, `FRATitle`, `FRAGeometryVersion`, `FRAEvidenceItem`, `SatelliteObservation`, `SchemeRuleSet`, and `DSSRecommendation`.
- Reuses `UUID_PK`, `GEOJSON_MULTIPOLYGON`, and `utcnow` from `app.db.models`.
- Later tasks depend on stable table and attribute names defined here.

- [ ] **Step 1: Write failing model tests**

```python
def test_staff_actor_and_rights_holder_are_distinct(session, user):
    holder = RightsHolder(display_name="Ramu Naik", holder_type="individual", claimant_category="ST")
    session.add(holder); session.flush()
    claim = FRAClaim(
        claim_number="FRA-OD-001", right_type="IFR", status="draft",
        rights_holder_id=holder.id, submitted_by=user.id,
    )
    session.add(claim); session.commit()
    assert claim.rights_holder_id != claim.submitted_by

def test_claim_retains_versioned_geometry_and_decisions(session, fra_claim, user):
    session.add_all([
        FRAGeometryVersion(claim_id=fra_claim.id, version=1, geometry=POLYGON,
                           source="claimant_sketch", provenance_json={"record": "A"},
                           boundary_quality="unverified", created_by=user.id),
        FRADecision(claim_id=fra_claim.id, authority_level="gram_sabha",
                    from_status="submitted", to_status="gram_sabha_verified",
                    outcome="verified", reasons_json=["Recorded in meeting"], actor_id=user.id),
    ])
    session.commit()
    assert len(fra_claim.geometry_versions) == 1
    assert len(fra_claim.decisions) == 1
```

- [ ] **Step 2: Run the focused tests and confirm import/model failures**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_database_models.py`

Expected: FAIL because `app.db.fra_models` does not exist.

- [ ] **Step 3: Implement the focused model module**

Define explicit SQLAlchemy models with relationships and these core constraints:

```python
class FRAClaim(Base):
    __tablename__ = "fra_claims"
    __table_args__ = (
        UniqueConstraint("claim_number", name="uq_fra_claim_number"),
        UniqueConstraint("legacy_claim_id", name="uq_fra_claim_legacy"),
    )
    id = mapped_column(UUID_PK, primary_key=True, default=uuid.uuid4)
    claim_number = mapped_column(String(100), nullable=False)
    right_type = mapped_column(String(16), nullable=False)
    status = mapped_column(String(32), default="draft", nullable=False)
    rights_holder_id = mapped_column(ForeignKey("rights_holders.id"), nullable=False)
    gram_sabha_id = mapped_column(ForeignKey("gram_sabhas.id"))
    submitted_by = mapped_column(ForeignKey("users.id"), nullable=False)
    legacy_claim_id = mapped_column(ForeignKey("claims.id"))
    parcel_id = mapped_column(ForeignKey("parcels.id"))
    document_id = mapped_column(ForeignKey("documents.id"))
    claimed_area_sqm = mapped_column(Numeric(16, 4))
    provenance_json = mapped_column(JSON, default=dict, nullable=False)
```

Use `UniqueConstraint("claim_id", "version")` for geometry and title versions,
`UniqueConstraint("provider", "scene_id", "claim_id", "asset_class")` for
observation idempotency, and an actor/idempotency unique constraint on DSS
recommendations. Keep source documents and satellite observations nullable on
evidence so oral, physical, documentary, and satellite evidence share one model.

- [ ] **Step 4: Import FRA metadata and add migration coverage**

Update `app/db/__init__.py` and `migrations/env.py` to import `fra_models`. Create
the migration with a fixed `TABLE_NAMES` list and create/drop only the new tables:

```python
TABLE_NAMES = [
    "rights_holders", "gram_sabhas", "fra_claims", "fra_decisions",
    "fra_geometry_versions", "satellite_observations", "fra_evidence_items",
    "fra_titles", "scheme_rule_sets", "dss_recommendations",
]

def upgrade():
    bind = op.get_bind()
    for name in TABLE_NAMES:
        Base.metadata.tables[name].create(bind, checkfirst=True)
```

Add a migration test that upgrades a fresh SQLite database to
`20260826_0003` and asserts every table exists.

- [ ] **Step 5: Run model and migration tests**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_database_models.py tests/test_migrations.py`

Expected: PASS.

- [ ] **Step 6: Commit the persistence slice**

```powershell
git add app/db/fra_models.py app/db/__init__.py migrations/env.py migrations/versions/20260826_0003_fra_foundation.py tests/test_fra_database_models.py tests/test_migrations.py
git commit -m "feat: add FRA foundation data model"
```

---

### Task 2: FRA lifecycle and versioning service

**Files:**
- Create: `app/services/fra_workflow.py`
- Create: `tests/test_fra_workflow.py`

**Interfaces:**
- Produces `allowed_transitions(status: str) -> set[str]`.
- Produces `transition_claim(session, claim, *, target_status, authority_level, outcome, reasons, actor_id, request_id) -> FRADecision`.
- Produces `issue_title(session, claim, *, title_number, geometry_version_id, issued_by, metadata) -> FRATitle`.

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_submitted_claim_can_be_verified_by_gram_sabha(session, claim, reviewer):
    claim.status = "submitted"
    decision = transition_claim(
        session, claim, target_status="gram_sabha_verified",
        authority_level="gram_sabha", outcome="verified",
        reasons=["Resolution GS-17"], actor_id=reviewer.id, request_id="req-1",
    )
    assert claim.status == "gram_sabha_verified"
    assert decision.from_status == "submitted"

def test_invalid_transition_preserves_claim_state(session, claim, reviewer):
    with pytest.raises(InvalidTransitionError) as error:
        transition_claim(session, claim, target_status="granted", authority_level="dlc",
                         outcome="granted", reasons=[], actor_id=reviewer.id, request_id="r")
    assert claim.status == "draft"
    assert "submitted" in error.value.allowed_states
```

- [ ] **Step 2: Run and confirm missing-service failure**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_workflow.py`

Expected: FAIL because `fra_workflow` does not exist.

- [ ] **Step 3: Implement the state machine and append-only decisions**

```python
TRANSITIONS = {
    "draft": {"submitted"},
    "submitted": {"gram_sabha_verified", "remanded", "withdrawn"},
    "gram_sabha_verified": {"sdlc_review", "remanded"},
    "sdlc_review": {"dlc_decided", "remanded"},
    "dlc_decided": {"granted", "rejected", "remanded"},
    "remanded": {"submitted", "withdrawn"},
    "granted": {"superseded"},
    "rejected": {"remanded"},
    "withdrawn": set(),
    "superseded": set(),
}
```

Reject unknown states and empty reasons for `rejected`, `remanded`, or
`superseded`. Add `FRADecision`, update the claim, and record an `AuditEvent` in
the same transaction. `issue_title` requires `granted`, increments the version,
deactivates the prior title without deleting it, and records an audit event.

- [ ] **Step 4: Add title-version tests and make them pass**

Test that a draft claim cannot receive a title and that issuing a corrected
title produces versions 1 and 2 with only version 2 active.

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_workflow.py`

Expected: PASS.

- [ ] **Step 5: Commit lifecycle behavior**

```powershell
git add app/services/fra_workflow.py tests/test_fra_workflow.py
git commit -m "feat: add FRA claim lifecycle"
```

---

### Task 3: FRA claim service and legacy promotion

**Files:**
- Create: `app/services/fra_claims.py`
- Create: `tests/test_fra_claims.py`

**Interfaces:**
- Produces `create_claim(session, *, claim_number, right_type, rights_holder_id, submitted_by, gram_sabha_id=None, parcel_id=None, document_id=None, claimed_area_sqm=None, provenance=None) -> FRAClaim`.
- Produces `promote_legacy_claim(session, *, legacy_claim_id, rights_holder_id, right_type, actor_id) -> FRAClaim`.
- Produces `add_geometry_version(session, claim, *, geometry, source, provenance, boundary_quality, actor_id) -> FRAGeometryVersion`.

- [ ] **Step 1: Write failing creation and promotion tests**

```python
def test_community_claim_requires_gram_sabha(session, holder, staff):
    with pytest.raises(FRAClaimValidationError, match="Gram Sabha"):
        create_claim(session, claim_number="CFR-1", right_type="CFR",
                     rights_holder_id=holder.id, submitted_by=staff.id)

def test_promoting_legacy_claim_reuses_evidence_and_is_idempotent(session, legacy_claim, holder, staff):
    first = promote_legacy_claim(session, legacy_claim_id=legacy_claim.id,
                                 rights_holder_id=holder.id, right_type="IFR", actor_id=staff.id)
    second = promote_legacy_claim(session, legacy_claim_id=legacy_claim.id,
                                  rights_holder_id=holder.id, right_type="IFR", actor_id=staff.id)
    assert first.id == second.id
    assert first.document_id == legacy_claim.document_id
    assert first.geometry_versions[0].geometry == legacy_claim.parcel.geometry
```

- [ ] **Step 2: Run and confirm missing-service failure**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_claims.py`

Expected: FAIL because `fra_claims` does not exist.

- [ ] **Step 3: Implement validation, versioning, and promotion**

Validate right types against `{"IFR", "CR", "CFR"}`. Require an individual or
household holder for IFR and a Gram Sabha plus community holder for CR/CFR.
Promotion generates `LEGACY-{legacy_claim.id}`, copies confirmed fields to
provenance, reuses parcel/document links, and creates geometry version 1.
Look up `legacy_claim_id` first to make the operation idempotent.

- [ ] **Step 4: Run claim-service tests and legacy regressions**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_claims.py tests/test_claim_service.py tests/test_land_api.py`

Expected: PASS.

- [ ] **Step 5: Commit the compatibility slice**

```powershell
git add app/services/fra_claims.py tests/test_fra_claims.py
git commit -m "feat: add FRA claims and legacy promotion"
```

---

### Task 4: Right-aware spatial policy

**Files:**
- Create: `app/services/fra_spatial_policy.py`
- Create: `tests/test_fra_spatial_policy.py`

**Interfaces:**
- Produces `SpatialFinding` and `SpatialEvaluation` dataclasses.
- Produces `evaluate_spatial_compatibility(session, claim, geometry, *, min_sqm, min_percent, policy_version="fra-spatial-v1") -> SpatialEvaluation`.
- Produces `_area_sqm(geometry) -> float` for SQLite development behavior.

- [ ] **Step 1: Write failing policy-matrix tests**

```python
@pytest.mark.parametrize((candidate, existing, expected), [
    ("IFR", "IFR", "blocked"),
    ("IFR", "CFR", "review_required"),
    ("CR", "IFR", "review_required"),
    ("CFR", "CFR", "review_required"),
])
def test_material_overlap_uses_right_type_policy(session, candidate, existing, expected):
    existing_claim = claim_with_geometry(session, right_type=existing, geometry=OVERLAP_A, status="submitted")
    candidate_claim = make_claim(session, right_type=candidate)
    result = evaluate_spatial_compatibility(session, candidate_claim, OVERLAP_B,
                                            min_sqm=1, min_percent=1)
    assert result.outcome == expected
    assert result.findings[0].related_claim_id == existing_claim.id

def test_sqlite_overlap_is_measured_in_square_metres():
    assert 10_000 < _area_sqm(SMALL_WGS84_SQUARE) < 15_000
```

- [ ] **Step 2: Run and confirm missing-policy failure**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_spatial_policy.py`

Expected: FAIL because `fra_spatial_policy` does not exist.

- [ ] **Step 3: Implement geometry measurement and the policy matrix**

For SQLite, transform WGS84 coordinates to a local equirectangular metre plane
centered on the combined geometry centroid:

```python
R = 6_371_008.8
def project(x, y, z=None):
    return math.radians(x) * R * math.cos(latitude_origin), math.radians(y) * R
```

Use Shapely intersection and projected areas. For PostgreSQL, query
`ST_Area(ST_Intersection(a, b)::geography)` and the smaller geography area.
Ignore statuses `draft`, `rejected`, `withdrawn`, and `superseded`. Same-parcel
IFR reuse is blocked even when geometry cannot be read. Record all material
findings and choose `blocked` over `review_required` over `allowed`.

- [ ] **Step 4: Run policy and legacy conflict tests**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_spatial_policy.py tests/test_claim_eligibility.py tests/test_spatial_conflicts.py`

Expected: PASS, with legacy exclusivity unchanged.

- [ ] **Step 5: Commit the spatial policy**

```powershell
git add app/services/fra_spatial_policy.py tests/test_fra_spatial_policy.py
git commit -m "feat: add FRA right-aware spatial policy"
```

---

### Task 5: Supporting satellite-evidence providers

**Files:**
- Create: `app/services/satellite_evidence.py`
- Create: `tests/test_satellite_evidence.py`

**Interfaces:**
- Produces `ImageryRequest`, `ImageryScene`, `AssetObservation`, and `AnalysisResult` dataclasses.
- Produces `ImageryProvider` and `AssetAnalyser` protocols.
- Produces `LocalManifestImageryProvider`, `LocalObservationAnalyser`, and `create_supporting_observations(...)`.

- [ ] **Step 1: Write failing provider and evidence tests**

```python
def test_local_observation_creates_supporting_unverified_evidence(session, fra_claim, staff):
    scene = ImageryScene(
        scene_id="scene-2005", provider="local-manifest", source_uri="private://scene-2005",
        acquired_at=date(2005, 1, 15), metadata={"observations": [
            {"asset_class": "agricultural_cover", "value": 0.72, "confidence": 0.83}
        ]},
    )
    observations = create_supporting_observations(
        session, fra_claim, scene=scene, analyser=LocalObservationAnalyser("local-v1"),
        actor_id=staff.id, request_id="sat-1",
    )
    evidence = observations[0].evidence_item
    assert evidence.legal_role == "supporting"
    assert evidence.source_verified is False
    assert "valid" not in evidence.description.casefold()

def test_provider_failure_creates_no_partial_records(session, fra_claim):
    with pytest.raises(SatelliteProviderUnavailable):
        acquire_and_analyse(session, fra_claim, provider=UnavailableProvider(), analyser=LocalObservationAnalyser("v1"))
    assert session.query(SatelliteObservation).count() == 0
```

- [ ] **Step 2: Run and confirm missing-provider failure**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_satellite_evidence.py`

Expected: FAIL because `satellite_evidence` does not exist.

- [ ] **Step 3: Implement protocols and deterministic local providers**

Validate asset classes against `agricultural_cover`, `forest_cover`,
`water_body`, and `homestead`. Validate confidence in `[0, 1]`, dates, provider,
scene ID, source URI, analyser version, and provenance. Persist observations and
linked evidence only after the complete analysis result validates. Reject
conclusion keys named `valid`, `invalid`, `approved`, `rejected`, or
`eligibility`.

- [ ] **Step 4: Run the satellite tests**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_satellite_evidence.py`

Expected: PASS.

- [ ] **Step 5: Commit the provider foundation**

```powershell
git add app/services/satellite_evidence.py tests/test_satellite_evidence.py
git commit -m "feat: add supporting satellite evidence foundation"
```

---

### Task 6: Versioned explainable DSS

**Files:**
- Create: `app/services/dss_engine.py`
- Create: `data/demo_dss_rules.json`
- Create: `tests/test_dss_engine.py`

**Interfaces:**
- Produces `validate_rule_definition(definition: dict) -> dict`.
- Produces `evaluate_condition(condition: dict, facts: dict) -> ConditionResult`.
- Produces `evaluate_rules(session, *, claim_id, facts, actor_id, idempotency_key) -> list[DSSRecommendation]`.

- [ ] **Step 1: Write failing rule-validation and evaluation tests**

```python
def test_missing_fact_returns_insufficient_data(session, water_rule, fra_claim, staff):
    result = evaluate_rules(session, claim_id=fra_claim.id, facts={"has_title": True},
                            actor_id=staff.id, idempotency_key="dss-1")[0]
    assert result.outcome == "insufficient_data"
    assert "water_body_present" in result.output_json["missing_inputs"]

def test_recommendation_retains_rule_version_and_reasons(session, water_rule, fra_claim, staff):
    result = evaluate_rules(
        session, claim_id=fra_claim.id,
        facts={"has_title": True, "water_body_present": False},
        actor_id=staff.id, idempotency_key="dss-2",
    )[0]
    assert result.outcome == "recommended"
    assert result.rule_version == "demo-1"
    assert result.output_json["advisory_only"] is True
    assert result.output_json["reasons"]

def test_rule_language_rejects_arbitrary_operator():
    with pytest.raises(InvalidRuleError):
        validate_rule_definition({"exec": "import os"})
```

- [ ] **Step 2: Run and confirm missing-engine failure**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_dss_engine.py`

Expected: FAIL because `dss_engine` does not exist.

- [ ] **Step 3: Implement the constrained recursive evaluator**

Allow only:

```python
OPERATORS = {"all", "any", "eq", "gte", "lte", "present", "absent"}
```

Leaf operations use `{"eq": {"fact": "has_title", "value": True}}`.
`all` and `any` contain non-empty condition arrays. Collect evaluated reasons
and missing fact names without treating missing as false. Return
`insufficient_data` when a required fact is missing, otherwise
`recommended` when the condition is true and `not_recommended` when false.
Persist exact input/output JSON and reuse an existing recommendation for the
same actor/idempotency key.

- [ ] **Step 4: Add three explicitly synthetic seed rules**

Create versioned demo rules for water support, housing support, and livelihood
support. Each source reference must start with `demo://` and each display name
must contain `Demo`. Tests assert the seed file cannot be mistaken for an
authoritative scheme rule.

- [ ] **Step 5: Run DSS tests**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_dss_engine.py`

Expected: PASS.

- [ ] **Step 6: Commit the DSS foundation**

```powershell
git add app/services/dss_engine.py data/demo_dss_rules.json tests/test_dss_engine.py
git commit -m "feat: add explainable FRA scheme DSS"
```

---

### Task 7: Protected FRA API and authorization

**Files:**
- Create: `app/models/fra_schemas.py`
- Create: `app/api/fra_routes.py`
- Modify: `app/api/auth.py`
- Modify: `app/main.py`
- Create: `tests/test_fra_api.py`

**Interfaces:**
- Exposes the routes specified in the design under `/api/fra`.
- Produces `require_reviewer()` accepting only `reviewer` and `admin` roles.
- Calls services from Tasks 2–6 and serializes privacy-safe responses.

- [ ] **Step 1: Write failing authentication and happy-path API tests**

```python
def test_fra_mutation_rejects_anonymous_client(client):
    assert client.post("/api/fra/rights-holders", json={"display_name": "Ramu", "holder_type": "individual"}).status_code == 401

def test_staff_can_create_holder_and_claim(client, staff_headers):
    holder = client.post("/api/fra/rights-holders", headers=staff_headers,
                         json={"display_name": "Ramu", "holder_type": "individual", "claimant_category": "ST"})
    claim = client.post("/api/fra/claims", headers=staff_headers, json={
        "claim_number": "IFR-001", "right_type": "IFR",
        "rights_holder_id": holder.json()["id"],
    })
    assert claim.status_code == 201
    assert claim.json()["submitted_by"] != claim.json()["rights_holder_id"]

def test_user_cannot_issue_title(client, staff_headers, granted_claim_payload):
    response = client.post(f"/api/fra/claims/{granted_claim_payload['id']}/titles",
                           headers=staff_headers, json={"title_number": "T-1"})
    assert response.status_code == 403
```

- [ ] **Step 2: Run and confirm route failures**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_api.py`

Expected: FAIL with 404 or missing imports because routes do not exist.

- [ ] **Step 3: Implement Pydantic contracts and route orchestration**

Use `Literal` values for right type, holder type, evidence category, asset
class, and lifecycle targets. Enforce geometry as GeoJSON `Polygon` or
`MultiPolygon`, normalize to `MultiPolygon`, and reject invalid geometry with
HTTP 422. Map `InvalidTransitionError` and blocked spatial evaluation to 409,
provider unavailability to 503, and invalid DSS definitions to 422.

Every mutation records an audit event either inside its service transaction or
at the route boundary, never both. Rights-holder responses omit external
references unless the requester is reviewer/admin. Satellite responses always
include `legal_role: supporting` and DSS responses always include
`advisory_only: true`.

- [ ] **Step 4: Add tests for every protected route and error mapping**

Cover legacy promotion, spatial review, satellite observation creation, admin
rule creation, DSS evaluation, transition authorization, title authorization,
idempotency, missing resources, and privacy-safe normal-user responses.

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_api.py`

Expected: PASS.

- [ ] **Step 5: Run current API regression tests**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_land_api.py tests/test_demo_auth.py tests/test_routes.py tests/test_land_routes.py`

Expected: PASS.

- [ ] **Step 6: Commit the API slice**

```powershell
git add app/models/fra_schemas.py app/api/fra_routes.py app/api/auth.py app/main.py tests/test_fra_api.py
git commit -m "feat: expose protected FRA foundation APIs"
```

---

### Task 8: Documentation, full verification, and clean handoff

**Files:**
- Modify: `README.md`
- Create: `docs/FRA_FOUNDATION.md`
- Modify: `docs/PRIVACY_RETENTION.md`
- Modify: `docs/OPERATIONS.md`
- Create: `tests/test_fra_documentation.py`

**Interfaces:**
- Documents the FRA data boundary, local satellite manifest, demo DSS rule import, lifecycle, roles, limitations, and verification commands.

- [ ] **Step 1: Write documentation assertions before documentation changes**

Create `tests/test_fra_documentation.py` with assertions that require these
exact warnings in `docs/FRA_FOUNDATION.md`:

```text
Satellite observations are supporting evidence and do not determine legal validity.
DSS recommendations are advisory and do not approve or sanction benefits.
```

Run the focused test and confirm it fails because the documentation does not
yet contain both statements.

- [ ] **Step 2: Document setup and boundaries**

Explain how to migrate, create reviewer/admin users, create an FRA claim,
promote a legacy claim, submit a local synthetic scene, load demo DSS rule sets,
evaluate recommendations, and distinguish demo outputs from authoritative data.

- [ ] **Step 3: Run focused documentation test**

Run: `.\venv\Scripts\python.exe -m pytest -q tests/test_fra_documentation.py`

Expected: PASS.

- [ ] **Step 4: Run full fresh verification**

```powershell
.\venv\Scripts\python.exe -m pytest -q
node --test tests/land_mapping_ui.test.js
.\venv\Scripts\python.exe -m compileall -q app scripts
.\venv\Scripts\python.exe -m alembic heads
docker compose config --quiet
git diff --check
```

Expected: all commands exit 0; pytest and Node report zero failures; Alembic
reports only `20260826_0003` as head.

- [ ] **Step 5: Review the specification requirement by requirement**

Confirm models, spatial matrix, legacy adapter, provider interfaces,
supporting-evidence safeguards, constrained DSS, role enforcement, audit events,
privacy, documentation, and regression compatibility each have a passing test.
Record any external-data limitation explicitly rather than claiming it is
implemented.

- [ ] **Step 6: Commit documentation and final verification changes**

```powershell
git add README.md docs/FRA_FOUNDATION.md docs/PRIVACY_RETENTION.md docs/OPERATIONS.md tests/test_fra_documentation.py
git commit -m "docs: document FRA platform foundation"
```
