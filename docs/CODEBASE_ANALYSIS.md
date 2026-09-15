# AranyaSetu implementation map

Analysis date: 2026-09-05. Repository revision: **0b8241e**. This describes the implementation at that revision, including observed gaps; design documents describe intent and are not treated as proof of working behavior.

## Scope and verification

Reviewed the first-party application, database models and migrations, browser code, scripts, sample data, dependency/deployment configuration, tests, and project documentation. Traced calls across API, service, persistence, worker, and browser boundaries. Excluded installed dependencies, model weights, Git internals, generated artifacts, secrets, private uploads, and the existing local database. Historical plans were used for context; this is not a line-by-line certification of historical documents or binary assets.

The codebase is a FastAPI modular monolith with a server-hosted JavaScript workspace, a separate durable-job worker, PostgreSQL/PostGIS persistence, and private document/artifact storage. Twelve migration revisions end at **20260906_0013**. The implementation is covered by Python service/API/integration tests, dependency-free browser-module tests, and an optional native PostGIS run.

Verification performed:

- Project virtual environment: **284 Python tests passed, plus 33 subtests**, in 34.35 seconds, using `venv/Scripts/python.exe -m pytest -q`.
- Browser logic uses dependency-free Node tests including `tests/cadastral_evidence_ui.test.js` and the FRA workspace modules.
- Additional isolated in-memory checks reproduced the access, intake-promotion, DSS-contract, asset-taxonomy, and location-filter findings below. These checks did not use the existing application database.

These results do not establish real-document OCR accuracy, browser rendering/accessibility across devices, production concurrency, or live PostGIS, ClamAV, S3, STAC, and external-model behavior. OCR/model calls are mocked or replaced by fixtures in relevant tests. Fiona is declared in requirements but was unavailable in the inspected local virtual environment, so actual Fiona-backed imports were not validated. No coverage percentage was measured.

No application source was changed during this analysis. The pre-existing untracked **app/static/fra/icons/cases.png** was left untouched.

## System shape

The repository is a **FastAPI modular monolith** named AranyaSetu. Its runtime, deployment, API catalogue, and primary UI communicate an FRA Spatial Intelligence and Decision Support platform with three substantial workflows:

1. Public OCR and optional evidence-constrained land-record enrichment.
2. Authenticated cadastral-evidence processing and an exclusive document-to-parcel link registry supporting FRA spatialization.
3. A Tamil Nadu FRA workspace for archive intake, native cases, maps, evidence, decisions, titles, assets, advisory scheme rules, referrals, and reports.

~~~mermaid
flowchart TD
    UI[Static browser workspaces] --> API[FastAPI routers]
    Public[Public OCR / enrichment] --> OCR[PaddleOCR and deterministic extraction]
    API --> Cadastral[Cadastral evidence OCR and parcel resolution]
    Cadastral --> Legacy[Reviewed legacy compatibility registry]
    Legacy --> Intake[FRA intake and reviewer triage]
    Intake --> Native[Native FRA claims]
    API --> Archive[Archive uploads and review]
    Archive --> Jobs[Database processing jobs]
    Jobs --> Extract[OCR and entity adapters]
    Extract --> Archive
    Archive --> Native
    API --> Native
    Native --> Evidence[Geometry versions and supporting evidence]
    Native --> Decisions[Human decisions and title versions]
    Evidence --> Facts[Derived fact snapshots]
    Decisions --> Facts
    Facts --> DSS[Versioned advisory rules and referrals]
    API --> Views[Atlas / dashboards / printable reports]
~~~

[app/main.py](../app/main.py) assembles 16 routers, middleware, static mounts, exception handlers, and health endpoints. Routes normally validate Pydantic inputs and roles, invoke synchronous services, and commit explicitly. SQLAlchemy sessions are request-scoped. Expensive FRA work uses persisted jobs; the public OCR and patta processing paths still perform synchronous OCR during the request.

The browser uses plain JavaScript, HTML, CSS, Leaflet, and Leaflet.draw. There is no React application, frontend compilation pipeline, or JavaScript package manifest. Maps depend on external library/CDN and tile services.

## Primary execution paths

### OCR and land-record enrichment

The public `/ocr` endpoint validates upload extension, MIME type, and byte size. Images are decoded to color arrays; PDFs are rendered at the configured DPI and OCR runs page by page. PaddleOCR is initialized lazily and cached, with Tamil recognition and CPU-oriented settings. Results include text, confidence, processing metadata, and review indicators. `/evaluate` computes normalized CER/WER and numeric/date/survey recall against supplied ground truth; OCR confidence is not ground-truth accuracy.

The image preprocessing module contains grayscale, scaling, thresholding, denoising, and deskew functions, but the active OCR path deliberately supplies the original color image. Tests assert this behavior. README descriptions of preprocessing do not accurately describe the current execution path.

`/land/extract` and `/ocr/land` provide a separate enrichment workflow. Deterministic code extracts survey/area candidates, coordinates, dates, and references. Optional LLM output is constrained by source evidence and does not authorize a parcel match or create geometry. Ambiguous coordinates remain unresolved; there is no automatic geocoding. Missing or failed LLM enrichment preserves deterministic results. Optional free-form LLM analysis on OCR is a separate feature from constrained land enrichment.

Key files: [document_intelligence_routes.py](../app/api/document_intelligence_routes.py), [cadastral_enrichment_routes.py](../app/api/cadastral_enrichment_routes.py), [ocr_engine.py](../app/services/ocr_engine.py), [image_processor.py](../app/services/image_processor.py), [pdf_processor.py](../app/services/pdf_processor.py), [quality_assessment.py](../app/services/quality_assessment.py), [evaluation.py](../app/services/evaluation.py), [cadastral_candidates.py](../app/services/cadastral_candidates.py), [cadastral_enrichment.py](../app/services/cadastral_enrichment.py), [llm_service.py](../app/services/llm_service.py).

### Supporting cadastral evidence and the exclusive registry

Authenticated `/api/cadastral-evidence/documents/process` handles supporting revenue/Patta evidence. It validates and scans the upload, saves it privately, runs OCR and deterministic cadastral-field extraction, resolves candidate parcels, and persists Document/OCRResult records. Actor-scoped idempotency permits replay. Failure cleanup removes newly stored files when the operation rolls back. The former Patta-named path remains an undocumented compatibility alias.

Parcel resolution uses the full administrative hierarchy plus survey number and subdivision. Normalization preserves meaningful identifiers such as leading zeros, supports verified aliases, and produces constrained fuzzy suggestions. Match scoring combines identifier agreement, OCR confidence, and area agreement. It uses imported cadastral geometry rather than synthesizing a boundary from document text.

`/api/cadastral-evidence/parcels/resolve` lets the owner submit corrected fields and records valid candidate IDs. `/api/cadastral-evidence/parcel-links` accepts only a candidate authorized for that owned document, applies exclusive-link and overlap rules, and creates an FRA intake item after successful legacy registration. PostgreSQL uses a global advisory lock and a unique parcel constraint to serialize the critical registration operation. The normal path rejects conflicts rather than creating a second parcel link. Older conflict-resolution/notification code remains in the repository.

The supporting parcel-registry interface shows registered document-to-parcel links and allows authenticated source-document retrieval. Originals use private storage, no-store responses, and access auditing. These records enter native FRA casework only through reviewed intake and normalization.

Key files: [cadastral_evidence_routes.py](../app/api/cadastral_evidence_routes.py), [cadastral_registry_routes.py](../app/api/cadastral_registry_routes.py), [cadastral_extraction.py](../app/services/cadastral_extraction.py), [parcel_normalization.py](../app/services/parcel_normalization.py), [parcel_resolver.py](../app/services/parcel_resolver.py), [cadastral_link_service.py](../app/services/cadastral_link_service.py), [cadastral_link_eligibility.py](../app/services/cadastral_link_eligibility.py), [cadastral_conflicts.py](../app/services/cadastral_conflicts.py), [parcel_importer.py](../app/services/parcel_importer.py), [alias_importer.py](../app/services/alias_importer.py).

### Native FRA claims and intake

Native FRAClaim records are separate from legacy Claim records. A legacy claim is not automatically an adjudicated FRA claim. Intake supports triage, negative dispositions with reasons, revision checks, and reviewed promotion. Archive records provide another promotion route. Native cases can also be created directly.

RightsHolder represents an individual, household, or community; User represents a system actor. IFR claims require an individual or household holder. CR/CFR claims require a community holder and Gram Sabha association. Claims may link a document, parcel, Gram Sabha, legacy claim, and successive geometry versions.

The implemented main lifecycle is:

`draft → submitted → gram_sabha_verified → sdlc_review → dlc_decided → granted or rejected`

Remand, resubmission, withdrawal, and supersession have explicitly allowed transitions. Adverse decisions require reasons. Reviewers/admins record decisions; a granted case can receive versioned titles, with the previous title deactivated. These are application workflow records. Authority levels are supplied metadata, not separate jurisdiction-specific permissions.

The native spatial evaluator distinguishes IFR exclusivity from overlapping community rights: IFR conflicts can produce `blocked`; CR/CFR interactions generally require review. Published reference layers contribute contextual findings. **Spatial evaluation is a separate operation: a blocked evaluation is not automatically enforced by the transition or title-issuance service.** This differs materially from the legacy registration path, which enforces its overlap check during creation.

Geometry, decision, evidence, and title histories are retained through versioned/appended records. Append-only behavior is mainly a service convention, not database-level immutability. Manual evidence begins unverified. Satellite observations are supporting evidence, not legal findings.

Key files: [fra_routes.py](../app/api/fra_routes.py), [fra_case_routes.py](../app/api/fra_case_routes.py), [fra_intake_routes.py](../app/api/fra_intake_routes.py), [fra_claims.py](../app/services/fra_claims.py), [fra_cases.py](../app/services/fra_cases.py), [fra_intake.py](../app/services/fra_intake.py), [fra_workflow.py](../app/services/fra_workflow.py), [fra_spatial_policy.py](../app/services/fra_spatial_policy.py), [fra_reference_spatial.py](../app/services/fra_reference_spatial.py), [satellite_evidence.py](../app/services/satellite_evidence.py).

### Archive digitization and background processing

Archive uploads support bounded batches, per-file validation and malware scanning, private documents, duplicate detection, partial success, and queued extraction. Each extraction retains raw text, fields, evidence, model/version provenance, and history. Reviewer corrections are distinct from extraction output. Promotion requires review and creates a draft native case; an imported source status such as granted is retained as provenance rather than automatically issuing a title.

ProcessingJob persists queued/running/completed/failed/quarantined states. PostgreSQL workers claim rows with `FOR UPDATE SKIP LOCKED`. A worker commits the running state before invoking a handler, then commits successful work or rolls back domain mutations before recording failure. Retries are bounded, with permanent errors quarantined. The CLI runs one job or a bounded number; Compose does not start a worker service.

Three handlers are wired: archive extraction, asset inference, and historical evidence. Reports are generated synchronously, despite the presence of a ReportArtifact model. Running jobs have no lease/recovery mechanism after worker termination, and automatic retries have no backoff delay.

Key files: [fra_archive.py](../app/services/fra_archive.py), [fra_document_intake.py](../app/services/fra_document_intake.py), [fra_entity_extraction.py](../app/services/fra_entity_extraction.py), [processing_jobs.py](../app/services/processing_jobs.py), [fra_job_handlers.py](../app/services/fra_job_handlers.py), [run_fra_jobs.py](../scripts/run_fra_jobs.py).

### Models, geospatial data, and historical imagery

ModelVersion records task type, configuration, version, checksum, and evaluation metadata. Activation checks readiness configuration and deactivates the prior active model for the task. It does not itself train a model, verify accuracy, or enforce a quality threshold.

| Capability | Current implementation |
| --- | --- |
| OCR | Direct PaddleOCR integration; the generic OCR protocol is not a complete runtime replacement mechanism. |
| FRA entity extraction | Local deterministic extraction and a configurable REST adapter; manifests support synthetic fixtures. |
| Asset inference | The worker selects ManifestAssetDetector and accepts synthetic manifest context. Registering a real model does not by itself provide image inference. |
| Historical discovery | Bounded, allow-listed STAC search and deterministic scene selection. |
| Historical analysis | REST processor integration can return versioned statistics and private artifacts. Missing processors yield an explicit insufficient-model result. |
| Model evaluation | CLI metrics over supplied labels/predictions; no training pipeline or demonstrated production accuracy. |

Historical jobs pin a geometry version, retain scene and acquisition provenance, store private artifacts, and support reviewer dispositions. They reject a changed current geometry during processing. A completed orchestration job may still report insufficient model/imagery; consumers must inspect the result, not only the job state.

Geospatial import stages GeoJSON and Fiona-supported KML, GeoPackage, or zipped Shapefile data, validates/repairs polygonal geometry, and requires explicit publication. Supported reference kinds are administrative boundaries, protected areas, forest compartments, water bodies, and cadastral parcels. Staged references do not automatically become legacy Parcel records or village profiles. Tamil Nadu is the only implemented state profile.

Key files: [model_gateway.py](../app/services/model_gateway.py), [fra_adapter_factory.py](../app/services/fra_adapter_factory.py), [fra_assets.py](../app/services/fra_assets.py), [historical_evidence.py](../app/services/historical_evidence.py), [stac_imagery.py](../app/services/stac_imagery.py), [fra_geospatial_import.py](../app/services/fra_geospatial_import.py), [state_profiles.py](../app/services/state_profiles.py), [evaluate_fra_models.py](../scripts/evaluate_fra_models.py).

### DSS, referrals, and reports

DSS derives immutable fact snapshots from claim state, active titles, intersecting village/reference information, recent verified assets, and reviewed imagery. Missing/stale evidence remains unknown. Water-source absence requires explicit sufficiently covered evidence; lack of detection alone is not treated as absence.

The rule engine uses a validated JSON condition language with all/any and comparison/presence operators. Rules carry versions and effective dates. Evaluations persist facts, explanations, missing inputs, and source identifiers, producing recommended, not_recommended, or insufficient_data. The derived-fact endpoint enforces case access; direct evaluation of caller-provided facts requires admin access.

SchemeCatalogEntry tracks separate versioned scheme descriptions and authoritative-source approval metadata. It does not automatically govern executable SchemeRuleSet activation. Referrals are persisted departmental workflow records with review/closure history; creating one does not send a message or sanction a benefit.

Village, case, archive, and historical reports are escaped HTML views with role-dependent information and no-store caching. Browser printing supplies PDF output. There is no background PDF generation pipeline.

Key files: [dss_facts.py](../app/services/dss_facts.py), [dss_engine.py](../app/services/dss_engine.py), [scheme_catalog.py](../app/services/scheme_catalog.py), [dss_referrals.py](../app/services/dss_referrals.py), [fra_reports.py](../app/services/fra_reports.py), [fra_dashboards.py](../app/services/fra_dashboards.py), [fra_atlas.py](../app/services/fra_atlas.py).

## Persistence and operations

| Model module | Tables and responsibilities |
| --- | --- |
| [models.py](../app/db/models.py), 9 tables | Users, parcels, administrative aliases, documents, OCR results, legacy claims/conflicts, audit events, notifications. |
| [fra_models.py](../app/db/fra_models.py), 10 tables | Gram Sabhas, rights holders, native claims, decisions, geometries, satellite observations, evidence, titles, scheme rules, recommendations. |
| [fra_completion_models.py](../app/db/fra_completion_models.py), 10 tables | Archive batches/records/extraction runs, model versions, village profiles, processing jobs, inference runs, assets, referrals, report artifacts. |
| [fra_operational_models.py](../app/db/fra_operational_models.py), 7 tables | Legacy intake, spatial import batches/features, imagery scenes/artifacts, DSS fact snapshots, scheme catalog entries. |

Geometry uses a custom SQLAlchemy MultiPolygon type: PostGIS geometry with SRID 4326 in PostgreSQL and JSON in SQLite. These backends are not spatially equivalent. Legacy SQLite overlap uses planar Shapely area in coordinate units, while the native FRA fallback projects to approximate metres. The explicit migration-created GiST index covers parcels; comparable indexes are absent for the newer geometry tables.

The migrations import current application metadata and call `create_all`/current table creation. Consequently, the first migration now sees all registered models, and historical revisions do not provide stable schema snapshots. Existing-table column evolution is not handled by `create_all`. Passing fresh-database tests should not be treated as proof that an older deployed schema upgrades correctly.

Configuration comes from cached Pydantic settings and environment/.env values. Production validation requires PostgreSQL, a non-placeholder sufficiently long auth secret, and mandatory scanning. Docker packages the API and runs migrations before Uvicorn. Compose supplies PostGIS, ClamAV, and persistent volumes for database, uploads, models, and scanner state. Readiness checks a database query only, not migration state, models, private storage, or scanner availability.

Authentication uses signed HS256 JWTs via bearer token or HttpOnly session cookie; roles are read from the database. Cookies are SameSite strict and Secure in production. Demo login uses a shared code and a shared reviewer identity; production validation does not force demo mode off. The example environment enables it. Logout clears the cookie without revoking an already issued token. No OIDC integration is implemented.

Local and S3 document stores keep uploads private; ClamAV can fail closed. Request IDs and audit records connect actions. Rate limiting is in-process and covers selected legacy API paths, not login, public OCR, or the FRA API. Privacy/retention documents state intended controls; automatic retention, legal-hold processing, and subject-request workflows are not implemented.

Scripts import parcel/alias/reference/scheme data, seed synthetic examples, manage users/tokens, run jobs, evaluate supplied model outputs, and wrap PostgreSQL backup/restore. The synthetic seed creates useful linked examples, but placeholder document keys are not real uploaded originals. It also refreshes older sample identifiers/history; it should be understood as a sample-data maintenance script, not an authoritative import. No seed, backup, or restore was run against the existing database during review.

## Browser organization

| Workspace | Main behavior and implementation |
| --- | --- |
| [login](../app/static/login/) | Four-digit staff-code login; successful login redirects to the FRA Overview. |
| [cadastral-evidence](../app/static/cadastral-evidence/) | Supporting document OCR review, parcel candidate selection, explicit confirmation, and persistent parcel-link map/list. |
| [fra/app.js](../app/static/fra/app.js) and [api.js](../app/static/fra/api.js) | Section navigation, shared Tamil Nadu context, custom events, same-origin requests, and error handling. |
| [archive.js](../app/static/fra/archive.js) | Batch upload, archive search, extracted-text inspection, reviewer corrections and promotion. |
| [cases.js](../app/static/fra/cases.js) | Intake triage, case detail tabs, drawn/imported boundaries, spatial evaluation, evidence, decisions, titles, and historical requests. |
| [atlas.js](../app/static/fra/atlas.js) and [assets.js](../app/static/fra/assets.js) | Filtered layers, village context, asset display, and synthetic inference submission. Asset-review APIs exist without a corresponding full reviewer UI. |
| [planner.js](../app/static/fra/planner.js) and [reports.js](../app/static/fra/reports.js) | Fact derivation, recommendation explanations, referral creation, and protected printable reports. |
| [dashboard.js](../app/static/fra/dashboard.js) | Planner aggregates and verifier queues. |

CSS contains responsive breakpoints, focus styles, and reduced-motion handling. Node tests exercise exported helpers and mocked DOM behavior; they do not provide actual browser layout or complete interactive-flow verification. Shared context is not uniformly applied by every panel, and several panels load only once or refresh manually. Job completion generally requires an explicit refresh.

## Findings established during analysis

The following are reproduced behavior, except where explicitly labeled code inspection.

| Finding | Evidence and consequence |
| --- | --- |
| **Case authorization differs between old and new routes** | A normal user received 404 for another user's `/api/fra/cases/{id}`, but 200 from `/api/fra/claims/{id}` and 201 when adding evidence to that claim. The foundation routes use existence checks; the newer case route uses `can_view_case`. Geometry writes have the same missing ownership check by inspection. This bypasses the access boundary expressed by the newer workspace. |
| **Direct legacy promotion bypasses intake review** | A normal user promoted a legacy claim through `/api/fra/claims/promote-legacy/{id}` while its intake remained awaiting_triage. The native case was created, but intake state and promoted-case linkage were unchanged. The newer intake workflow requires reviewer triage and revision checks. |
| **Seeded DSS rules and derived facts have incompatible names** | All three sample rule conditions evaluated to unknown against a real derived snapshot. They request has_title, water_body_present, homestead_present, and agricultural_cover; derivation emits has_active_title, water_source_present, homestead_observation, and agricultural_observation. Seeded recommendations use separately supplied facts, masking the mismatch in the normal derive-and-evaluate flow. |
| **Asset and reference taxonomies do not align with DSS** | A current verified agricultural_cover asset produced unknown/no_verified_source for agricultural_observation. The fact mapper expects agricultural_land/cropland/agriculture. DSS also queries water_stress/groundwater reference kinds that the import allowlist rejects. Existing fact tests directly insert matching internal records and therefore miss these producer-consumer gaps. |
| **Dashboard location filters omit valid cases** | A case located through its rights holder's Gram Sabha appeared in the Salem case list but not the dashboard's Salem claim IDs. Case lookup falls back through holder/parcel locations; dashboard filtering relies on the claim's direct Gram Sabha. |
| **Worker crash recovery is incomplete — code inspection** | Running state is committed before handler execution. No lease, timeout recovery, or retry route for running jobs exists, so termination can strand a job. Compose also requires an operator to arrange worker execution. |
| **Migration history is coupled to current models — code inspection** | Revision 0001 uses current Base metadata, and later revisions create current table objects. This weakens reproducibility and leaves existing-column changes outside the migration strategy. |

Additional implementation concerns to retain for future work:

- Public OCR has byte limits but no PDF page/rendered-pixel budget; it reads the upload before size rejection and performs blocking model work inside async request handlers. Its paths lack the protected intake's malware-scanning and rate-limiting behavior.
- User creation CLI permits only user/admin even though reviewer is an implemented role and appears in operational instructions. Demo authentication and OCR preprocessing documentation also need reconciliation with code.
- Revision comparisons in review services are ordinary application checks, not atomic compare-and-swap updates or SQLAlchemy optimistic-version columns. Concurrency beyond tested sequential conflicts remains unverified.
- The legacy parcel unique constraint is unconditional, while service-level active-claim checks exclude rejected/superseded records. The database can therefore reject reuse that the service considers eligible.
- The historical handler hardcodes its collection/cloud choices instead of fully honoring the corresponding discovery settings. Some UI idempotency keys omit changed inputs such as boundary version or selected asset class, potentially replaying an earlier job.
- Archive location indexing is populated by review for real extraction flows; synthetic fixtures prepopulate it. Location filters can therefore hide unreviewed real uploads that remain visible in unfiltered queues.
- Dashboard counts have bounded queue inputs and incomplete job/entity coverage. Their labels should not be assumed to represent exhaustive operational totals at scale.

## Guidance for subsequent changes

Start from the user-visible workflow, then follow its router, schema, service, model, and browser consumer together. Similar names across legacy and native FRA code do not imply identical authorization or business rules. Preserve document/extraction provenance, distinguish staff actors from rights holders, and check every promotion route when changing intake behavior.

The most consequential follow-up is to unify case authorization and promotion rules across route generations, then align asset/reference/fact/rule contracts. Add integration tests that create data through its actual producer API before consuming it in DSS, dashboards, or case views. Address worker recovery and stable migrations before relying on long-lived deployments. Existing synthetic scenarios and green unit tests provide a useful foundation, but several cross-module contracts need these end-to-end checks.
