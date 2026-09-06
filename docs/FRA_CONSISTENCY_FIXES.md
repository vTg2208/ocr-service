# FRA consistency fixes — September 2026

> Historical completion note: migration heads and test counts below describe the 2026-09-05 checkpoint. See `ARCHITECTURE.md` and `IMPLEMENTATION_CHECKLIST.md` for the current system.

This change completes the two immediate remediation steps from the codebase assessment: case/review integrity and consistency between the existing asset, DSS, Atlas and dashboard workflows. It uses the existing schema and dependencies.

## Access and review integrity

- Case details, linked evidence, geometry, historical imagery, asset jobs, recommendations and case reports follow the same owner/reviewer access policy. Hidden cases return 404; reviewer-only actions return 403. List filters narrow permitted records before returning results.
- Both legacy-promotion routes require an existing reviewed intake and its current revision. A matching replay is idempotent; conflicting promotion parameters are rejected.
- Intake, archive extraction/review/promotion, asset review/correction and referrals reserve a revision with a conditional database update before writing side effects. Concurrent stale requests return 409 and roll back the transaction, including audits and any new holder, case or correction.
- Native lifecycle, title and geometry operations guard the claim status and update timestamp. Title and geometry collections reload after the guard so version allocation uses current data. Historical reviews use their review timestamp as the concurrency token.
- Spatial dispositions require a reviewer and the current saved geometry version. The server marks these sources verified. Older unversioned/unverified evidence stays in history but does not clear the current-boundary review queue. Editing or switching the browser boundary invalidates the pending evaluation.

## Data contracts and current summaries

Asset inference, review, filters and fact derivation share the supported 33-class vocabulary, with explicit aliases for historical names. Agricultural cover is a numeric fraction separate from agricultural presence. Accepted measurement forms are `{"cover_fraction": 0.6}`, `{"coverage_fraction": 0.6}` and scalar `0.6` (stored as `{"value": 0.6}`). Fractions must be finite numbers in [0, 1]; a boolean never supplies a measured fraction. Presence-only observations leave numeric cover unknown. Corrections preserve the original model output.

New facts use `tn-facts-v2`; sample rules use `tn-sample-2` and consume those actual fact names. Unverified, superseded, stale or incompatible observations do not become positive eligibility inputs. Historical imagery supplies a current fact only when linked to the current geometry. Water-stress, groundwater and groundwater-stress references can pass through import/publication into derivation. Sources remain attached to the resulting snapshot.

Sample seeding derives facts through the application service. It preserves existing reviewed measurements, extraction evidence, rule definitions, snapshots and recommendations. The supplied unverified/old observations legitimately produce missing inputs. Sample rules remain advisory examples.

Cases, Atlas, assets, recommendation lists and dashboards resolve location through claim Gram Sabha, holder Gram Sabha, then parcel. Hierarchy filters trim whitespace and compare without case; Tamil Nadu and TN are accepted state names. Browser context changes reload visible panels and invalidate hidden panels while preserving applicable local filters/selections. Older asynchronous responses cannot replace the current context.

Dashboard totals count the full scoped population; each displayed queue is capped at 100. Queues include village assets and failed/overdue village and archive-record jobs. Planner summaries use the latest fact snapshot per claim and recommendation per claim/scheme, and count a missing input once per claim. Historical records and referral history remain stored.

## Client compatibility

| Operation | Required/current contract |
| --- | --- |
| Legacy intake promotion, including compatibility route | `expected_revision` from the reviewed intake |
| Archive promotion | JSON body containing `expected_revision` from the reviewed archive record |
| Historical imagery review | Required `expected_reviewed_at`; send null on the first review, then the returned timestamp |
| Spatial disposition evidence | `provenance.geometry_version_id` identifying the current saved boundary |
| New derived evaluation | `derivation_version: "tn-facts-v2"` (also the API default) |
| Existing v1 fact replay | Same claim, version and idempotency key reuses the persisted snapshot; new v1 derivations return 422 |
| Recommendation list | Latest per claim/scheme by default; `include_history=true` exposes history, subject to access controls |
| New rule evaluation | Latest registered active definition per scheme by default; explicit `rule_set_ids` select particular active versions |

The browser and seed callers use the updated contracts. A stale write should be retried only after reloading and reviewing the latest record.

## Verification

Final verification in the original checkout: **360 Python tests and 73 subtests passed**, plus **52 JavaScript tests passed**. The regular Python run skips 12 native PostgreSQL tests; those pass in the separate Docker run below.

Python coverage includes separate database sessions/connections, stale writes, transaction rollback, actual inference → review → facts → sample rules, reference import/publication, location fallbacks, queue populations above 100, and retained history. JavaScript tests execute the actual scripts in a simulated DOM with deferred responses. Rendered browser checks use a temporary SQLite database containing synthetic data.

Live verification on **PostgreSQL 16.4 / PostGIS 3.4.3** (`postgis/postgis:16-3.4`): **179 tests and 45 subtests passed**. A fresh database migrated through all five Alembic revisions to `20260902_0005`. The runner redirects the existing FRA test fixtures to the migrated PostgreSQL database, retaining real service, API and SQL behavior. Synthetic application data is cleared between tests.

Native checks verify geometry round trips, SRID 4326, validity, intersection and geography area. Overlapping transactions use independent connections and confirm the competing writer appears in `pg_blocking_pids` before releasing the first transaction. Coverage includes claim decisions, geometry versions, titles, archive promotion, asset corrections and nullable historical-review timestamps. Commit and rollback cases confirm stale writes fail, rolled-back writes can be retried, and related records/audits are not duplicated.

The live run exposed a validation-order issue: non-finite numbers could reach an asset job's JSON payload before validation. Model output validation now rejects them before persistence, and asset inference/correction endpoints return 422 while leaving jobs, revisions and observations unchanged. Regression tests cover NaN, positive infinity and negative infinity. The sample-history test now enables SQLite's foreign-key PRAGMA only on SQLite; PostgreSQL enforces those constraints directly.

To repeat the live checks from the repository root with Docker running:

```powershell
& .\venv\Scripts\python.exe scripts/test_postgres.py
```

The runner creates a uniquely named container with a temporary password and a random port bound to localhost, then removes its container and temporary volume in cleanup. Additional pytest options are accepted, for example `-k nonfinite`. No application database, uploaded documents, credentials or existing containers were changed.
