# Forest Rights Act foundation

AranyaSetu now includes a protected, production-shaped foundation for individual forest rights (IFR), community rights (CR), and community forest resource rights (CFR). It models rights holders, Gram Sabhas, claims, append-only decisions, versioned geometries and titles, evidence, satellite observations, and explainable scheme recommendations.

This is infrastructure for an authorized FRA workflow. It is not a legal decision-maker and does not replace a Forest Rights Committee, Gram Sabha, SDLC, DLC, or responsible department.

> **Satellite observations are supporting evidence and do not determine legal validity.**
>
> **DSS recommendations are advisory and do not approve or sanction benefits.**

## Boundaries and compatibility

The original patta workflow and `/api/claims` routes remain unchanged. They keep their exclusive-parcel rule. Native FRA behavior is isolated under `/api/fra/*` and uses a right-aware spatial policy:

- material IFR-to-IFR overlap is blocked;
- overlap involving CR or CFR is sent for human review because FRA rights can be layered or shared;
- inactive draft, rejected, withdrawn, and superseded claims do not block another claim;
- geometry findings never change claim status automatically.

Staff `User` records identify authenticated actors. `RightsHolder` records identify the person, household, or community whose rights are being recorded. These are deliberately separate records.

## Setup and roles

Apply the latest migration and create development identities:

```powershell
alembic upgrade head
python -m scripts.create_user fra-staff --display-name "FRA Staff" --role user
python -m scripts.create_user fra-reviewer --display-name "FRA Reviewer" --role reviewer
python -m scripts.create_user fra-admin --display-name "FRA Administrator" --role admin
python -m scripts.mint_dev_token fra-staff --minutes 60
```

All FRA routes require a database-backed authenticated user. A `user` can enter claims and evidence. A `reviewer` or `admin` is required for lifecycle decisions and title issuance. Only an `admin` can create DSS rule sets. The HS256 token and development-user scripts are local adapters; use the authority's approved identity provider in production.

## Claim workflow

The lifecycle is explicit and append-only:

```text
draft -> submitted -> gram_sabha_verified -> sdlc_review
      -> dlc_decided -> granted or rejected
```

Remand, withdrawal, and supersession paths are validated by the service. Rejection, remand, and supersession require reasons. Each accepted transition creates a decision and audit event. Issuing a corrected title creates another title version and deactivates, but does not delete, the earlier version.

Typical API order:

1. `POST /api/fra/rights-holders`
2. `POST /api/fra/gram-sabhas` for a CR or CFR claim
3. `POST /api/fra/claims`
4. `POST /api/fra/claims/{claim_id}/geometries`
5. `POST /api/fra/claims/{claim_id}/evidence`
6. `POST /api/fra/claims/{claim_id}/spatial-evaluation`
7. Reviewer calls `POST /api/fra/claims/{claim_id}/transitions`
8. After grant, reviewer calls `POST /api/fra/claims/{claim_id}/titles`

GeoJSON `Polygon` and `MultiPolygon` inputs are validated and stored as WGS84 `MultiPolygon` values. SQLite uses a local projected Shapely calculation for development; PostgreSQL/PostGIS uses geography-based square-metre calculations for production-shaped evaluation.

## Promoting a legacy claim

`POST /api/fra/claims/promote-legacy/{legacy_claim_id}` accepts a rights-holder ID, an FRA right type, and a Gram Sabha ID when required. It reuses the original private document and parcel links, copies confirmed fields into provenance, and creates geometry version 1 from the cadastral parcel. Repeating the operation returns the same FRA claim through the unique legacy link.

Promotion is a compatibility adapter. It does not assert that the legacy patta claim has been legally recognized under the FRA.

## Local satellite manifest

The satellite endpoint accepts only an explicit synthetic/local scene manifest. It does not fetch imagery and the local analyser does not inspect pixels. A request supplies a private source URI, acquisition date, analyser version, and deterministic observations, for example:

```json
{
  "scene_id": "synthetic-scene-2005",
  "provider": "local-manifest",
  "source_uri": "private://synthetic-scene-2005",
  "acquired_at": "2005-01-15",
  "analyser_version": "local-v1",
  "observations": [
    {"asset_class": "forest_cover", "value": 0.72, "confidence": 0.83}
  ]
}
```

Submit it to `POST /api/fra/claims/{claim_id}/satellite-observations` after the claim has a geometry version. Every observation creates linked evidence with `legal_role: supporting`, `verification_state: unverified`, and `source_verified: false`. Source URIs are persisted as private provenance and omitted from API responses. Missing manifests return `503` without partial records. Automated fields such as `valid`, `approved`, or `eligibility` are rejected.

Real imagery providers and scientifically validated analysers must be implemented, reviewed, licensed, and calibrated outside this demo adapter before any operational use.

## Demo DSS rules

[`data/demo_dss_rules.json`](../data/demo_dss_rules.json) contains visibly synthetic water, housing, and livelihood rules. Post each object to `POST /api/fra/dss/rule-sets` with an administrator token. The API validates the constrained `all`, `any`, `eq`, `gte`, `lte`, `present`, and `absent` operators before persistence.

Evaluate active rules with an `Idempotency-Key` header:

```json
{
  "claim_id": "00000000-0000-0000-0000-000000000000",
  "facts": {"has_title": true, "water_body_present": false}
}
```

`POST /api/fra/dss/evaluate` returns `recommended`, `not_recommended`, or `insufficient_data`, plus reasons, missing inputs, the exact rule version, source reference, and an advisory disclaimer. The engine has no arbitrary code, SQL, template, or network execution. Requests and outputs are stored for reproducibility; repeat calls by the same actor and rule with the same idempotency key return the existing recommendation.

The bundled rules are examples only. An authorized department must approve authoritative scheme rules, effective dates, source references, data definitions, and review procedures.

## Privacy and audit

Normal users do not receive rights-holder external references. Reviewers and administrators can see them in protected claim detail. Satellite source URIs and private documents are not returned by these routes. Mutations create metadata-only audit events with the actor and request ID; raw documents are never copied into audit rows.

Before production, define lawful basis, notices, access restrictions, retention and legal-hold rules, authoritative data ownership, incident response, and independent legal/security/privacy review. See [Privacy and retention](PRIVACY_RETENTION.md) and [Operations](OPERATIONS.md).

## Verification

```powershell
python -m pytest -q
node --test tests/land_mapping_ui.test.js
python -m compileall -q app scripts
python -m alembic heads
docker compose config --quiet
git diff --check
```

The expected Alembic head is `20260826_0003`. Passing local tests demonstrates software behavior with synthetic data; it does not validate remote-sensing accuracy, legal sufficiency, scheme authority, or production integrations.
