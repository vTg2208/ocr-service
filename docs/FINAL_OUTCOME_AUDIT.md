# Final priority outcome audit

This audit maps the backlog's final P0/P1/P2 order to the implemented and
verified repository state. `verified` means the corresponding code path and
tests exist. It does not claim legal approval, model accuracy, authoritative
scheme eligibility, or production readiness.

## P0 — core outcomes

| ID | Outcome | Status | Evidence or boundary |
| --- | --- | --- | --- |
| P0-1 | FRA-centred positioning | verified | AranyaSetu opens on FRA Overview and follows legacy FRA → structured FRA → spatial FRA → Atlas → asset intelligence → DSS |
| P0-2 | Legacy ingestion | verified | Scan/PDF batches, CSV/XLSX registers, private originals, row/page provenance, and staged spatial imports |
| P0-3 | FRA normalization | verified | Reviewed archive fields map to native holder, village, IFR/CR/CFR, status, dates, area, decision, geometry, and title records |
| P0-4 | Authorization | verified | Owner visibility plus reviewer/admin controls across archive, cases, spatial data, assets, DSS, reports, and operations |
| P0-5 | Legacy promotion workflow | verified | Reviewed records promote idempotently with duplicate detection and preserved source-to-native provenance |
| P0-6 | FRA Atlas | verified | Claims, titles, villages, reviewed assets, imagery coverage, reference layers, synchronized filters, and progress statistics |
| P0-7 | Real satellite AI pipeline | verified_with_user_model_placeholder | Real Sentinel-2 STAC/COG ingestion and the production model contract are implemented; trained asset inference remains `awaiting_user_model` at the user's direction |
| P0-8 | Asset taxonomy | verified | `fra-assets-v1` is enforced from model input through persistence, Atlas, profiles, DSS, and reports |
| P0-9 | DSS fact/rule mismatch | verified | `fra-dss-facts-v1`, rule validation, freshness, exclusions, priorities, assets, and catalogue governance share one contract |
| P0-10 | One working DSS use case | verified | The water convergence journey persists reviewed evidence, derives facts, evaluates a governed rule, and exposes advisory reasoning |
| P0-11 | End-to-end integration tests | verified | Six named producer-to-consumer journeys cover document intake through reports; full Python/browser and native PostGIS suites pass |

## P1 — operational completion

| ID | Outcome | Status | Evidence or boundary |
| --- | --- | --- | --- |
| P1-12 | Village asset profiles | verified | Persisted `village-assets-v2` profiles use reviewed observations and explicit observation gaps |
| P1-13 | Scheme convergence | verified | Claim/holder/village projection distinguishes candidates, not indicated, insufficient data, and unevaluated schemes |
| P1-14 | Review UI | verified | Field correction/approval/rejection, geometry and case review, asset disposition, and DSS referrals are integrated |
| P1-15 | Evidence/provenance UI | verified | Field, source, page/row, model/version/confidence, correction, geometry, imagery, asset, and rule lineage are visible by role |
| P1-16 | Dashboard | verified | FRA, spatial, development, DSS, missing-input, referral, and verifier-queue summaries use shared hierarchy filters |
| P1-17 | Spatial indexes | verified | Native PostGIS geometry columns have migration-owned GIST indexes and zero schema drift |
| P1-18 | Worker recovery | verified | Leases, heartbeats, fencing, `SKIP LOCKED`, stale recovery, bounded retry, quarantine, and reviewer retry are implemented |
| P1-19 | Stable migrations | verified | Twelve frozen revisions upgrade/downgrade and end at `20260906_0013`; SQLite and PostGIS drift checks pass |
| P1-20 | Real Tamil Nadu pilot dataset | verified_with_disclosed_source_gaps | Real public village geometry and Sentinel-2 metadata are included; claim document/person/title geometry remain clearly synthetic or unavailable rather than fabricated |

## P2 — later scope

| ID | Outcome | Status | Current boundary |
| --- | --- | --- | --- |
| P2-21 | Additional satellite models | future_scope | The registered model gateway can add evaluated adapters after the user's first detector is attached |
| P2-22 | More reference layers | partially_available | Forest, protected area, compartment, water, groundwater, infrastructure, cadastral, and administrative contracts are supported; authoritative coverage is deployment data work |
| P2-23 | More schemes | partially_available | PM-KISAN, MGNREGA, PMAY-G, JJM, and DAJGUA catalogue structures exist as draft/non-authoritative examples |
| P2-24 | Advanced AI recommendations | future_scope | Current recommendations use explainable governed rules over versioned facts |
| P2-25 | Historical satellite analysis | verified | Bounded historical discovery, model processing, artifact review, and evidence reports are implemented |
| P2-26 | Mobile feedback | future_scope | No mobile/community feedback client is implemented |
| P2-27 | IoT integration | future_scope | No sensor ingestion contract or operational adapter is implemented |
| P2-28 | Real-time satellite monitoring | future_scope | Current Sentinel-2 ingestion is explicit and job-driven; no continuous monitoring scheduler is implemented |

## Verification baseline

The final baseline includes the complete Python suite, the dependency-free
browser suite, a disposable PostgreSQL 16/PostGIS 3.4 suite with Alembic drift
checks, `compileall`, current Markdown-link resolution, Compose rendering, a
healthy `aranyasetu` API/worker stack, the idempotent 17-step pilot, and live
authenticated checks of FRA Overview, Cadastral Evidence, OpenAPI route
positioning, and the generated village report.

The remaining external inputs are intentional and visible: the user's trained
asset detector, authoritative claim-level FRA data/geometries, approved scheme
catalogue/rules, and production identity/licensing/governance decisions.
