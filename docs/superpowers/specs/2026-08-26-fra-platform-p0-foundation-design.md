# FRA Platform P0 Foundation Design

## Purpose

Extend AranyaSetu from a patta-to-parcel registry demonstration into a
production-shaped Forest Rights Act foundation without breaking the existing
claim workflow. The new foundation models FRA actors, right types, evidence,
decisions, titles, geometries, satellite observations, and explainable scheme
recommendations. Real satellite providers, trained models, identity providers,
and government integrations remain replaceable external adapters.

## Scope

This design implements four outcomes:

1. A native FRA domain for IFR, CR, and CFR claims.
2. Right-type-aware spatial compatibility instead of blanket overlap rejection.
3. A pluggable satellite-observation pipeline that creates supporting evidence.
4. A versioned, rule-based DSS that produces explainable recommendations.

The current `Claim` model, `/api/claims` endpoints, and browser workflow remain
available. New functionality is exposed under `/api/fra/*`.

## Non-goals

- Training or shipping a real remote-sensing model.
- Fetching commercial or government satellite imagery.
- Determining whether an FRA claim is legally valid.
- Replacing Gram Sabha, FRC, SDLC, or DLC decision-making.
- Automatically approving or sanctioning a government benefit.
- Integrating with a production identity provider or government beneficiary API.
- Replacing the existing browser workflow in this delivery.

## Architectural Approach

The existing registry remains a compatibility layer. New FRA services and
tables form a separate domain boundary and reuse existing `User`, `Document`,
and `Parcel` records where appropriate.

```text
Existing browser and /api/claims
        -> existing Claim workflow (unchanged)
        -> promotion adapter
              -> FRA claim domain

/api/fra/claims
        -> FRA claim service
        -> evidence and decision services
        -> spatial policy engine
        -> PostgreSQL/PostGIS or SQLite development storage

/api/fra/satellite-observations
        -> imagery provider interface
        -> analyser provider interface
        -> supporting EvidenceItem

/api/fra/dss/recommendations
        -> versioned scheme rules
        -> declared facts + titles + observations
        -> recommendation, reasons, and missing inputs
```

## Domain Model

### Rights holders and communities

`RightsHolder` represents an individual, household, organization, or community.
It stores a display name, holder category, optional ST/OTFD classification, and
external reference. It does not reuse `User`: a staff actor and an FRA claimant
are different concepts.

`GramSabha` represents the community institution responsible for community
claims. It stores its name, village and administrative references, and optional
boundary geometry. A community `RightsHolder` may reference a `GramSabha`.

### FRA claims

`FRAClaim` contains:

- claim number and right type (`IFR`, `CR`, or `CFR`);
- current lifecycle state;
- rights holder and optional Gram Sabha;
- submitting staff actor;
- optional legacy `Claim`, cadastral `Parcel`, and source `Document` links;
- claimed area and timestamps.

The lifecycle states are:

```text
draft -> submitted -> gram_sabha_verified -> sdlc_review -> dlc_decided
                                                   |             |
                                                   v             v
                                                remanded       granted/rejected
```

Transitions are validated by a state machine. Every transition creates an
append-only `FRADecision` record with actor, authority level, outcome, reasons,
and timestamp. A granted claim can receive one active `FRATitle`; corrections
create a new title version rather than overwriting history.

### Evidence and geometry

`FRAEvidenceItem` stores an evidence category, source, description, document
link, observation link, provenance, capture date, and verification state.
Satellite-derived evidence is always marked `supporting` and
`source_verified = false` until an authorized human records verification.

`FRAGeometryVersion` stores the claim geometry, source, version number,
provenance, boundary quality, creation actor, and creation timestamp. A claim
may retain multiple geometry versions while exposing one current version.

## Spatial Compatibility Policy

Spatial evaluation returns one of three outcomes:

- `allowed`: no material incompatibility was found;
- `review_required`: the overlap can represent layered or shared rights;
- `blocked`: the new geometry duplicates an exclusive active right.

The initial policy matrix is:

| Candidate | Existing | Result |
|---|---|---|
| IFR | IFR | Block when the same parcel is reused or the material-overlap threshold is met |
| IFR | CR/CFR | Review required |
| CR | IFR/CR/CFR | Review required |
| CFR | IFR/CR/CFR | Review required |

Draft, rejected, superseded, and withdrawn records do not block new claims but
remain visible to reviewers. The evaluation records the rule version, overlap
area and percentage, related claim IDs, and reasons. SQLite calculates geodesic
area through a projection-independent helper rather than comparing degrees
squared with square-metre thresholds. PostgreSQL continues to use PostGIS
`geography` calculations.

## Legacy Compatibility

`POST /api/fra/claims/promote-legacy/{claim_id}` creates an FRA claim from an
existing accepted legacy claim. Promotion:

- reuses the original document and parcel;
- records the authenticated user as submitting actor, not rights holder;
- requires a supplied rights-holder ID and FRA right type;
- copies confirmed fields into provenance metadata;
- creates the initial geometry version from the cadastral parcel;
- is idempotent through a unique legacy-claim link.

No existing table or endpoint changes meaning. Existing blanket exclusivity
continues only inside the legacy demonstration route.

## Satellite Observation Foundation

Two provider interfaces isolate external dependencies:

```python
class ImageryProvider(Protocol):
    def acquire(self, request: ImageryRequest) -> ImageryScene: ...

class AssetAnalyser(Protocol):
    def analyse(self, scene: ImageryScene, geometry: dict) -> AnalysisResult: ...
```

The local provider accepts a synthetic scene manifest. The local analyser
accepts deterministic observations such as water-body presence, agricultural
cover percentage, forest-cover percentage, homestead presence, acquisition
date, confidence, model version, and source URI. It does not fabricate pixels
or claim model inference.

`SatelliteObservation` stores the requested geometry, scene metadata, asset
class, observed value, confidence, provider, analyser version, acquisition
date, processing time, and provenance. Creating an observation also creates a
linked `FRAEvidenceItem` with legal role `supporting`.

The API never returns `valid`, `invalid`, `approved`, or `rejected` as an
automated satellite conclusion.

## Explainable DSS Foundation

`SchemeRuleSet` stores a scheme code, display name, version, effective dates,
required facts, conditions, recommendation text, and source reference.
Rules use a constrained JSON condition language with `all`, `any`, `eq`,
`gte`, `lte`, `present`, and `absent` operators. Arbitrary code execution is
not permitted.

The DSS accepts a claim or title plus declared facts. It may also read current
satellite observations when a rule explicitly names them. For each active rule
set it returns:

- `recommended`, `not_recommended`, or `insufficient_data`;
- the rule and version used;
- reasons that evaluated true or false;
- missing inputs;
- source references;
- a statement that the result is advisory and requires departmental review.

Every evaluation is persisted as `DSSRecommendation` with its exact inputs and
outputs. Re-evaluation creates a new record, preserving the historical rule
version.

Seed rules use synthetic/local examples for water-support, housing-support,
and livelihood-support recommendations. Their labels state that they are demo
rules and not authoritative eligibility criteria.

## API Surface

The protected API provides:

```text
POST   /api/fra/rights-holders
POST   /api/fra/gram-sabhas
POST   /api/fra/claims
GET    /api/fra/claims/{claim_id}
POST   /api/fra/claims/{claim_id}/geometries
POST   /api/fra/claims/{claim_id}/evidence
POST   /api/fra/claims/{claim_id}/transitions
POST   /api/fra/claims/{claim_id}/titles
POST   /api/fra/claims/promote-legacy/{legacy_claim_id}
POST   /api/fra/claims/{claim_id}/spatial-evaluation
POST   /api/fra/claims/{claim_id}/satellite-observations
POST   /api/fra/dss/rule-sets
POST   /api/fra/dss/evaluate
GET    /api/fra/dss/recommendations/{recommendation_id}
```

All mutation endpoints require authentication and record audit events. Admin
authorization is required to create rule sets. Human verification, claim
decisions, and title issuance require `reviewer` or `admin` roles.

## Validation and Error Handling

- Invalid lifecycle transitions return HTTP 409 with the allowed next states.
- A blocked spatial evaluation returns HTTP 409 with privacy-safe reasons.
- A review-required overlap does not silently approve submission; it returns
  the related claim IDs only to authorized reviewers.
- Missing imagery or analyser providers return HTTP 503 without creating
  partial evidence.
- Invalid DSS rules return HTTP 422 before persistence.
- Missing DSS inputs produce `insufficient_data`, not a negative eligibility
  conclusion.
- Database mutations use transactions and idempotency keys where repeatable
  client submission is expected.

## Security and Privacy

- Rights-holder data never appears in legacy public parcel responses.
- FRA endpoints require authenticated database-backed users.
- Rights-holder external references are not placed in logs.
- Satellite source URIs and evidence documents are private metadata.
- DSS audit records retain identifiers and rule inputs but not raw document
  contents.
- Existing private-storage and metadata-only logging rules remain in force.

## Testing Strategy

Development follows test-driven cycles. Tests cover:

- staff actors remain distinct from rights holders;
- IFR, CR, and CFR creation and validation;
- valid and invalid lifecycle transitions;
- append-only decisions and versioned titles/geometries;
- legacy promotion and idempotency;
- every cell of the spatial policy matrix;
- square-metre overlap behavior in SQLite and PostGIS-shaped query behavior;
- satellite provider failures and supporting-evidence labelling;
- prohibition of automated legal-validity conclusions;
- DSS rule validation, missing data, explanations, rule versioning, and audit;
- role enforcement and privacy-safe responses;
- migration upgrade and current legacy regression tests.

The full existing Python and JavaScript suites must remain green. New APIs use
FastAPI integration tests with SQLite; PostGIS SQL generation and Docker-backed
integration checks are added where the environment permits.

## Delivery Boundaries

Completion means the schema, migrations, services, protected APIs, local
providers, synthetic seed data, documentation, and automated tests exist and
work together. It does not mean that satellite observations are scientifically
validated or that DSS rules are legally authoritative. Those claims require
external data, validation, and authority approval outside this repository.
