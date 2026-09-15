# Forest Rights Act workflow reference

AranyaSetu models the review lifecycle for Individual Forest Rights (IFR),
Community Rights (CR), and Community Forest Resource Rights (CFR). The full
system architecture, data model, evidence contracts, and limitations are in
[`ARCHITECTURE.md`](ARCHITECTURE.md).

The platform supports authorized work; it does not replace a Forest Rights
Committee, Gram Sabha, SDLC, DLC, or responsible line department. Satellite
observations are supporting evidence. DSS outputs are advisory and never
approve a right, determine scheme eligibility, or sanction a benefit.

## FRA-centred boundaries

Native FRA behavior is under `/api/fra/*`. The separate
`/cadastral-evidence` workspace supports survey/subdivision matching and parcel
identification. Its records can enter FRA casework only through controlled
legacy intake and promotion. Earlier `/api/pattas`, `/api/parcels`, and
`/api/claims` routes remain undocumented compatibility aliases.

Staff `User` records identify authenticated actors. `RightsHolder` records
identify the individual, household, community, or Gram Sabha whose rights are
being recorded. They are separate records with different access rules.

## Lifecycle

```text
draft -> submitted -> gram_sabha_verified -> sdlc_review
      -> dlc_decided -> granted or rejected
```

Remand, withdrawal, and supersession paths are validated. Decisions are
append-only. Rejection, remand, and supersession require reasons. Corrected
titles create a new version and deactivate the prior version without deleting
history.

Typical native case order:

1. Create or match the rights holder and Gram Sabha.
2. Create an IFR, CR, or CFR claim.
3. Add a versioned Polygon/MultiPolygon claim boundary.
4. Attach evidence and run right-aware spatial evaluation.
5. Record reviewer-controlled Gram Sabha, SDLC, and DLC transitions.
6. Issue a versioned title only after the claim is granted.

Material IFR-to-IFR overlap is blocked. CR/CFR overlap is returned as a human
review finding because community rights may be layered. Spatial evaluation does
not change lifecycle state.

## Legacy promotion

Legacy scans, PDFs, CSV registers, and XLSX registers enter the archive review
queue. OCR/entity or tabular extraction results must be corrected and approved
before promotion. Promotion normalizes right type, status, areas, dates,
authority, holder, village, and supporting survey/subdivision evidence; it also
detects duplicates and retains the complete source-to-native mapping.

`POST /api/fra/claims/promote-legacy/{legacy_claim_id}` remains available for
reviewed cadastral compatibility records. It never treats an ordinary Patta as
an FRA title.

## Satellite, assets, and Atlas

`POST /api/fra/claims/{claim_id}/imagery-ingestions` queues real bounded
Sentinel-2 analysis-ready ingestion for a spatial claim. Historical-evidence
routes separately discover and process time-separated supporting imagery.
Private artifact locations and provider URLs are redacted.

Asset records use `fra-assets-v1`; village aggregates use
`village-assets-v2`. The user-supplied trained detector is not attached, so
production inference is explicitly `awaiting_user_model`. No fixture detection
is substituted.

The FRA Atlas combines administrative boundaries, villages, claims, titles,
reviewed assets, imagery footprints, and published forest, water, groundwater,
infrastructure, cadastral, and other reference layers. Shared filters drive the
map and progress statistics.

## DSS and scheme convergence

Normal evaluations derive `fra-dss-facts-v1` from the selected claim, active
title, reviewed village assets, imagery coverage, and published references.
Missing and stale values remain unknown. Rules validate required facts/assets,
exclusions, priorities, freshness, and outcome logic.

An executable rule must link to an active authoritative scheme catalogue
version. Bundled scheme entries and rules are examples until an authorized
department supplies approval metadata. Results preserve evidence, reasons,
missing inputs, fact/rule/catalogue versions, and referral history.

## Roles and local verification

Users can submit records within their visibility scope. Reviewer/admin access
is required for extraction approval, legacy promotion, lifecycle decisions,
titles, reference publication, asset review, and referrals. Model and scheme
administration has additional route-specific controls. Production must replace
the demo access-code and HS256 adapters with the approved identity provider.

```powershell
alembic upgrade head
python -m pytest -q
node --test tests
python -m compileall -q app scripts
python -m alembic heads
docker compose config --quiet
```

The expected Alembic head is `20260906_0013`. Passing tests verifies software
contracts; it does not validate legal sufficiency, OCR/model accuracy,
authoritative scheme policy, or production data licensing.

See [`MODEL_ADAPTERS.md`](MODEL_ADAPTERS.md),
[`PRIVACY_RETENTION.md`](PRIVACY_RETENTION.md), and
[`OPERATIONS.md`](OPERATIONS.md) for the corresponding integration and
operational controls.
