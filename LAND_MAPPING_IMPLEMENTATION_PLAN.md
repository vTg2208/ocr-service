# Patta-to-Parcel Mapping and Land-Claim Conflict Management

> **Superseded claim rule (2026-08-26):** The implemented registry no longer records a second claim as a conflict. It rejects exact-parcel and material polygon-overlap attempts before insertion, keeps the accepted polygon persistent, and links that polygon to its privately stored patta. See `docs/superpowers/specs/2026-08-26-exclusive-land-claims-design.md` for the current behavior. Historical conflict-management notes below are retained only as background.

## Implementation Brief

Implement an MVP that allows a user to upload a patta, extracts its parcel details through the existing OCR service, locates the corresponding cadastral parcel, displays the parcel boundary on a map, and records the user's claim in a central database. If another active claim already refers to the same parcel or overlaps it spatially, create a conflict record for administrative review.

This document is intended to be usable as an implementation prompt for a coding agent working in this repository.

## Important Domain Rule

A value such as `701/4B` is normally a survey and subdivision identifier, not an area measurement.

Parse it as:

```json
{
  "survey_number": "701",
  "subdivision_number": "4B"
}
```

It is not globally unique. A reliable parcel lookup normally requires this composite key:

```text
state + district + taluk + village + survey_number + subdivision_number
```

If the document only contains a numeric area, such as `0.42 hectares`, the system cannot determine the parcel's geographic location automatically. It must obtain a survey identifier and administrative location from the document or ask the user to provide or confirm them.

The system must distinguish between:

- A `parcel`: a geographic cadastral record with a polygon.
- A `claim`: a user's assertion that a document relates them to that parcel.

Uploading a document must never directly change the registered owner of a parcel. Conflict detection creates review cases; it does not decide legal ownership.

## Existing Project Integration Points

The repository already contains the initial OCR and land-data extraction pipeline:

- `app/api/routes.py`: base `/ocr` endpoint.
- `app/api/land_routes.py`: land extraction endpoints, including `/land/extract` and `/ocr/land`.
- `app/services/land_candidates.py`: deterministic extraction of survey numbers, areas, dates, locations, references, and coordinates.
- `app/services/land_enrichment.py`: construction of structured land records and optional LLM validation.
- `app/services/quality_assessment.py`: OCR quality checks.
- `app/models/response_models.py`: OCR and land extraction response models.

Preserve the existing OCR endpoint as an independent operation. Add parcel resolution and claim registration after structured land extraction.

## Target User Flow

```text
Patta upload
    -> Existing OCR processing
    -> Structured field extraction
    -> Field normalization
    -> User confirmation when required
    -> Seeded cadastral parcel lookup
    -> Unique parcel match
    -> Parcel polygon displayed on map
    -> User submits claim
    -> Exact-parcel and spatial-overlap checks
    -> Claim and any conflicts saved centrally
```

When no unique parcel is found:

```text
No match
    -> Show missing or unmatched fields
    -> Ask user to correct administrative location or survey details

Multiple matches
    -> Show candidate parcels
    -> Require user selection or administrator review

Low-confidence OCR
    -> Require user confirmation before parcel resolution
```

## Recommended Technology

- Keep the existing Python API framework and project conventions.
- Use PostgreSQL with the PostGIS extension as the central database.
- Use SQLAlchemy/GeoAlchemy2 and the repository's existing migration system if present.
- Use GeoJSON for API geometry responses.
- Use Leaflet for the MVP web map.
- Use OpenStreetMap tiles for development, subject to the tile provider's usage policy.
- Store cadastral parcel polygons in the database; do not model land using only a marker or center coordinate.

## Data Model

### Parcels

The parcel registry is seeded before users upload documents. Each parcel represents one cadastral boundary.

```sql
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE parcels (
    id UUID PRIMARY KEY,
    state TEXT NOT NULL,
    district TEXT NOT NULL,
    taluk TEXT NOT NULL,
    village TEXT NOT NULL,
    survey_number TEXT NOT NULL,
    subdivision_number TEXT NOT NULL DEFAULT '',
    official_area_sqm NUMERIC,
    geometry geometry(MultiPolygon, 4326) NOT NULL,
    source TEXT NOT NULL,
    source_version TEXT,
    source_record_id TEXT,
    boundary_quality TEXT DEFAULT 'unknown',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (
        state,
        district,
        taluk,
        village,
        survey_number,
        subdivision_number
    )
);

CREATE INDEX parcels_geometry_gix ON parcels USING GIST (geometry);
CREATE INDEX parcels_lookup_idx ON parcels (
    state,
    district,
    taluk,
    village,
    survey_number,
    subdivision_number
);
```

Use EPSG:4326 for display and interchange. For area calculations, cast to `geography` or transform to an appropriate projected coordinate reference system before calculating square metres.

### Users

Use the project's existing user model if one exists. Otherwise add a minimal authenticated user record. Do not expose personal data through general map endpoints.

### Documents

```text
id
uploaded_by
storage_key
original_filename
content_type
sha256
ocr_status
created_at
```

Do not store uploaded documents in a publicly accessible directory. Persist a checksum so duplicate uploads can be detected.

### OCR Results

```text
id
document_id
raw_text
overall_confidence
structured_result_json
extractor_version
created_at
```

Keep the original OCR text and evidence snippets. Normalized values must not replace the raw evidence.

### Claims

```sql
CREATE TABLE claims (
    id UUID PRIMARY KEY,
    claimant_id UUID NOT NULL,
    parcel_id UUID NOT NULL REFERENCES parcels(id),
    document_id UUID NOT NULL,
    claimed_area_sqm NUMERIC,
    status TEXT NOT NULL DEFAULT 'pending',
    match_confidence NUMERIC,
    match_method TEXT NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_by UUID,
    reviewed_at TIMESTAMPTZ,
    review_notes TEXT
);
```

Supported statuses:

```text
pending
matched
needs_review
conflicting
verified
rejected
superseded
```

Do not enforce one claim per parcel. Multiple claims are necessary to represent and investigate conflicts.

### Claim Conflicts

```sql
CREATE TABLE claim_conflicts (
    id UUID PRIMARY KEY,
    claim_a_id UUID NOT NULL REFERENCES claims(id),
    claim_b_id UUID NOT NULL REFERENCES claims(id),
    conflict_type TEXT NOT NULL,
    overlap_area_sqm NUMERIC,
    overlap_percent NUMERIC,
    status TEXT NOT NULL DEFAULT 'open',
    resolution_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    UNIQUE (claim_a_id, claim_b_id, conflict_type)
);
```

Conflict types:

```text
same_parcel
spatial_overlap
area_mismatch
duplicate_document
```

Store claim IDs in a deterministic order before inserting a conflict so `(A, B)` and `(B, A)` cannot create duplicates.

### Audit Events

Create an append-only audit table containing:

```text
id
actor_id
action
entity_type
entity_id
before_json
after_json
created_at
request_id
```

Record uploads, extraction corrections, parcel selections, claim submissions, status changes, and conflict resolutions.

## Cadastral Data Seeding

### Required Input

Accept parcel data from at least GeoJSON for the MVP. Shapefile and KML support may be added later.

Each source record must be mapped to:

```json
{
  "state": "Tamil Nadu",
  "district": "Thanjavur",
  "taluk": "Kumbakonam",
  "village": "Example Village",
  "survey_number": "701",
  "subdivision_number": "4B",
  "official_area_sqm": 1200,
  "geometry": {
    "type": "MultiPolygon",
    "coordinates": []
  },
  "source": "authoritative source name",
  "source_version": "2026-01"
}
```

### Import Requirements

- Validate that every geometry is a Polygon or MultiPolygon.
- Convert Polygon values to MultiPolygon when necessary.
- Reject empty geometries.
- Repair invalid geometries only when safe, and report every repaired record.
- Normalize administrative names and parcel identifiers before insertion.
- Upsert using the composite parcel key.
- Preserve source and version metadata.
- Produce counts for inserted, updated, skipped, invalid, and duplicate records.
- Make imports idempotent.

For the initial vertical slice, seed 50 to 200 parcels from one village. A small synthetic GeoJSON dataset may be used for development, but records must be clearly marked as synthetic and must never be presented as authoritative cadastral data.

## OCR and Field Normalization

Extend the existing structured extraction to return:

```json
{
  "state": "Tamil Nadu",
  "district": "Thanjavur",
  "taluk": "Kumbakonam",
  "village": "Example Village",
  "survey_number": "701",
  "subdivision_number": "4B",
  "document_area_sqm": 1200,
  "confidence": 0.91,
  "evidence": {
    "district": "District: Thanjavur",
    "village": "Village: Example Village",
    "survey_number": "Survey No. 701/4B",
    "area": "Extent: 0.12.00 hectares"
  }
}
```

### Survey Number Rules

Normalize equivalent input forms:

```text
701/4b   -> survey_number=701, subdivision_number=4B
701 / 4 B -> survey_number=701, subdivision_number=4B
701-4B   -> survey_number=701, subdivision_number=4B
```

- Trim whitespace.
- Uppercase subdivision letters.
- Preserve meaningful leading zeros unless the cadastral dataset defines a canonical removal rule.
- Never silently convert ambiguous OCR characters such as `B` and `8`.
- Produce alternatives or require confirmation when ambiguity exists.

### Administrative Name Rules

- Normalize case and repeated whitespace.
- Maintain an alias table for alternate English spellings and local-language names.
- Resolve an alias to a canonical registry value.
- Preserve the original extracted text as evidence.
- Use fuzzy matching only to suggest candidates, never to silently choose a parcel.

### Area Rules

Convert supported measurements to square metres while preserving the original value and unit.

At minimum support:

```text
square metres
hectares
acres
cents
```

Unit conversion must be explicit and tested. Treat area as a supporting validation signal, not a parcel identifier.

## Parcel Resolution

Create a parcel resolver service separate from OCR extraction.

### Resolution Stages

1. Validate that the minimum lookup fields are present.
2. Canonicalize administrative names through the alias registry.
3. Search for an exact composite-key match.
4. If no exact match exists, search for safe candidate suggestions.
5. Compare document area with registry area when both are available.
6. Return a unique match, multiple candidates, no match, or insufficient data.

### Resolution Statuses

```text
matched
multiple_matches
not_found
insufficient_data
needs_confirmation
```

### Confidence Policy

Use an explainable score based on field agreement. Do not allow an LLM to invent a parcel or coordinates.

Suggested scoring inputs:

```text
exact state match
exact district match
exact taluk match
exact village or verified alias match
exact survey number match
exact subdivision match
area difference percentage
OCR confidence for each source field
```

Automatic matching should require exact survey/subdivision and a unique administrative-location match. Fuzzy matches should require confirmation.

Area difference:

```text
abs(document_area_sqm - official_area_sqm) / official_area_sqm * 100
```

Make the acceptable area tolerance configurable. An area mismatch should reduce confidence or create a review warning; it should not automatically substitute another parcel.

### Resolution Response

```json
{
  "status": "matched",
  "parcel": {
    "id": "uuid",
    "state": "Tamil Nadu",
    "district": "Thanjavur",
    "taluk": "Kumbakonam",
    "village": "Example Village",
    "survey_number": "701",
    "subdivision_number": "4B",
    "official_area_sqm": 1180,
    "geometry": {
      "type": "MultiPolygon",
      "coordinates": []
    }
  },
  "match_confidence": 0.94,
  "match_method": "exact_composite_key",
  "area_difference_percent": 1.69,
  "warnings": [],
  "alternatives": []
}
```

## Conflict Detection

Run conflict detection inside the same database transaction used to create a claim where practical.

### Exact-Parcel Conflict

Find other active claims with the same `parcel_id`. Exclude rejected and superseded claims. Create a `same_parcel` conflict for each applicable existing claim and set the new claim to `conflicting`.

### Spatial-Overlap Conflict

Spatial conflict detection is useful when a claim can contain a user-adjusted or externally supplied polygon, or when cadastral records themselves overlap.

Use PostGIS operations:

```sql
ST_Intersects(a.geometry, b.geometry)
ST_Intersection(a.geometry, b.geometry)
ST_Area(intersection::geography)
```

Calculate overlap percentage against the smaller of the two parcel areas:

```text
overlap_area / min(parcel_a_area, parcel_b_area) * 100
```

Ignore negligible sliver overlaps below configurable square-metre and percentage thresholds. These commonly result from data precision differences.

### Conflict Response

```json
{
  "claim_id": "uuid",
  "status": "conflicting",
  "conflicts": [
    {
      "id": "uuid",
      "type": "same_parcel",
      "existing_claim_id": "uuid",
      "overlap_area_sqm": 1180,
      "overlap_percent": 100,
      "status": "open"
    }
  ]
}
```

Do not return another claimant's private information to a normal user. A user-facing response may state that an existing claim requires review. Detailed claimant data is available only to authorized administrators.

## API Design

Follow existing routing and response-model conventions.

### Upload and Process a Patta

```http
POST /api/pattas/process
Content-Type: multipart/form-data
```

Responsibilities:

- Validate and store the document securely.
- Run the existing OCR pipeline.
- Run structured land extraction.
- Normalize the parcel reference.
- Attempt parcel resolution.
- Persist the document and OCR result.
- Return extracted fields, evidence, resolution status, and parcel geometry when matched.
- Do not create a claim yet; the user must confirm the extracted details.

### Resolve Corrected Fields

```http
POST /api/parcels/resolve
Content-Type: application/json
```

Input:

```json
{
  "document_id": "uuid",
  "state": "Tamil Nadu",
  "district": "Thanjavur",
  "taluk": "Kumbakonam",
  "village": "Example Village",
  "survey_number": "701",
  "subdivision_number": "4B",
  "document_area_sqm": 1200
}
```

Use this endpoint after a user corrects an OCR field or chooses among candidates.

### Fetch Parcel

```http
GET /api/parcels/{parcel_id}
```

Return parcel metadata and GeoJSON geometry. Do not return claimant information.

### Submit Claim

```http
POST /api/claims
Content-Type: application/json
```

Input:

```json
{
  "document_id": "uuid",
  "parcel_id": "uuid",
  "confirmed_fields": {
    "state": "Tamil Nadu",
    "district": "Thanjavur",
    "taluk": "Kumbakonam",
    "village": "Example Village",
    "survey_number": "701",
    "subdivision_number": "4B",
    "document_area_sqm": 1200
  }
}
```

Responsibilities:

- Verify the document belongs to the authenticated user.
- Verify the selected parcel was returned as a valid resolution candidate.
- Create the claim idempotently.
- Run conflict detection.
- Write audit events.
- Return claim status and privacy-safe conflict information.

### Current User's Claims

```http
GET /api/claims/mine
```

### Administrative Conflict Queue

```http
GET /api/admin/conflicts
GET /api/admin/conflicts/{conflict_id}
PATCH /api/admin/conflicts/{conflict_id}
```

Protect all administrative endpoints with role-based authorization.

## Minimal UI

Build one focused responsive workflow rather than a large dashboard.

### Step 1: Upload

- Drag-and-drop zone and file picker.
- Accept the formats already supported by the OCR service.
- Show upload and processing states.
- Explain that extracted details will be shown for confirmation.

### Step 2: Confirm Extracted Fields

Show editable fields for:

```text
State
District
Taluk
Village
Survey number
Subdivision number
Document area
```

- Display confidence and source evidence beside uncertain fields.
- Highlight missing and low-confidence values.
- Re-run parcel resolution after a correction.
- Do not make users edit raw OCR text.

### Step 3: Map the Parcel

Use Leaflet to:

- Fit the map to the returned parcel polygon.
- Render the selected parcel with a strong outline and translucent fill.
- Show survey/subdivision, village, official area, and document area.
- Show an area mismatch warning when applicable.
- Render alternative candidate parcels when multiple matches exist.
- Avoid exposing other claimants' identities.

### Step 4: Submit Claim

- Require the user to confirm the parcel and extracted data.
- Disable submission until a parcel is selected.
- Show success, pending-review, or conflict status.
- If a conflict exists, explain that the claim was recorded and requires review.

### Suggested Layout

```text
+-------------------------------------------------------+
| Upload Patta                                         |
| [ Drop a file here or choose a file ]                |
+--------------------------+----------------------------+
| Extracted information    | Parcel map                 |
| State                    |                            |
| District                 | Highlighted polygon        |
| Taluk                    |                            |
| Village                  | Match and area summary     |
| Survey: 701 / 4B         |                            |
| Area                     |                            |
+--------------------------+----------------------------+
| Warnings and evidence                                 |
| [Confirm and register claim]                          |
+-------------------------------------------------------+
```

On mobile, stack the upload, extracted fields, map, and submission sections vertically. Give the map an explicit height so it remains visible.

## Central Synchronization

For the MVP, synchronization means every client reads and writes through the same API and central PostgreSQL/PostGIS database:

```text
Web or mobile clients -> API -> PostgreSQL/PostGIS
```

Do not implement peer-to-peer synchronization. If offline support is later required, add a client-side submission queue with server-generated versions and explicit conflict handling.

Required central-system safeguards:

- Authentication.
- Role-based authorization.
- Idempotency keys for uploads and claim submission.
- Database transactions around claim creation and conflict detection.
- Append-only auditing.
- Secure document storage.
- Encryption in transit and at rest.
- Backup and restore procedures.
- Cadastral source and version tracking.
- Restricted access to personally identifiable information.
- Request IDs and structured logs.

## Implementation Phases

### Phase 1: End-to-End Vertical Slice

Goal: prove the entire workflow for one village.

- Configure PostgreSQL/PostGIS.
- Add parcel, document, OCR-result, claim, conflict, alias, and audit models.
- Add migrations.
- Create a GeoJSON parcel importer.
- Seed 50 to 200 parcels for one village.
- Extend extraction and normalization for full parcel references.
- Implement exact composite-key resolution.
- Implement exact-parcel conflict detection.
- Build the upload, confirmation, map, and submit-claim UI.
- Keep fuzzy matching and advanced administrative tools out of this phase.

Phase 1 acceptance scenario:

1. A seeded parcel exists for `Example Village`, survey `701`, subdivision `4B`.
2. A user uploads a patta containing that reference.
3. OCR and normalization produce `701` and `4B`.
4. The API resolves the unique parcel.
5. The UI displays its polygon.
6. The user confirms and submits a claim.
7. A second user's claim for the same parcel is recorded as conflicting.
8. Neither user receives the other user's private information.

### Phase 2: Matching Reliability

- Add administrative-name aliases.
- Add local-language aliases where needed.
- Add safe candidate suggestions for spelling variations.
- Add area comparison and configurable tolerance.
- Handle ambiguous `B/8`, `O/0`, and similar OCR results.
- Add multiple-match selection.
- Add an administrator review queue.

### Phase 3: Spatial Conflict Management

- Add spatial-overlap queries.
- Add sliver-overlap tolerances.
- Show conflicting parcel boundaries to administrators.
- Add conflict notes, evidence comparison, and resolution history.
- Add notifications without exposing claimant details.

### Phase 4: Production Hardening and Expansion

- Import additional villages and districts.
- Add cadastral dataset versioning and re-import workflows.
- Add object storage and malware scanning for uploads.
- Add rate limits and file-size limits.
- Add backup/restore testing.
- Add monitoring and operational alerts.
- Add retention and privacy policies.
- Perform security and authorization review.

## Test Requirements

### Unit Tests

- Survey/subdivision parsing for spacing, slashes, hyphens, and letter case.
- Ambiguous OCR character handling.
- Administrative alias normalization.
- Every supported area conversion.
- Area-difference calculations.
- Match-confidence calculations.
- Conflict pair ordering and deduplication.
- Privacy-safe API serialization.

### Integration Tests

- GeoJSON import and idempotent re-import.
- Exact parcel resolution.
- No-match, multiple-match, and insufficient-data responses.
- Claim creation and exact-parcel conflict creation.
- Transaction rollback if conflict creation fails.
- Spatial-overlap thresholds.
- Authorization for documents, claims, and administrator routes.
- Duplicate request handling through idempotency keys.

### UI Tests

- Upload success and failure.
- Low-confidence field correction.
- Unique match map rendering.
- Multiple-candidate selection.
- Mobile layout.
- Claim success and conflict messaging.
- No leakage of other claimant details.

## Definition of Done for the Immediate Goal

The immediate goal is complete when:

- A cadastral GeoJSON dataset can be imported into PostGIS.
- OCR output is normalized into a complete parcel lookup key.
- A unique seeded parcel can be resolved from an uploaded patta.
- The parcel polygon is displayed automatically in the UI.
- The user can confirm and centrally register a claim.
- A second active claim for the same parcel creates a conflict.
- Claims and conflicts are visible to an authorized administrator.
- Normal users cannot see another claimant's identity or document.
- Every important action has an audit event.
- Tests cover identifier normalization, parcel resolution, claim creation, conflict detection, authorization, and privacy-safe responses.

## Constraints for the Implementing Agent

- Work with the repository's existing architecture and coding conventions.
- Preserve the existing `/ocr` behavior.
- Keep deterministic extraction and evidence validation as the source of truth.
- Do not allow an LLM to generate parcel coordinates or select a parcel unsupported by the seeded registry.
- Store polygon geometry, not only a point.
- Do not infer ownership from an upload or from OCR output.
- Do not overwrite another user's claim.
- Do not expose claimants' personal data through map or public parcel APIs.
- Keep the first implementation scoped to one seeded village and one complete end-to-end workflow.
- Use configuration for area tolerances, overlap thresholds, upload limits, and automatic-match confidence thresholds.
- Document setup, migrations, parcel import, and local startup in the project README.

## Recommended First Implementation Sequence

1. Inspect the existing dependency, database, frontend, and migration setup.
2. Add PostGIS-compatible persistence and migrations.
3. Add the parcel GeoJSON importer and one small development dataset.
4. Extend land extraction models with normalized administrative and parcel fields.
5. Implement the deterministic parcel resolver.
6. Add document and OCR-result persistence.
7. Add claim creation and exact-parcel conflict detection.
8. Add the process, resolve, parcel, claim, and administrator APIs.
9. Build the minimal upload-to-map UI.
10. Add focused unit, integration, authorization, and UI tests.
11. Document local setup and the end-to-end demonstration flow.

The first demonstrable milestone should be: upload a patta referencing `village + 701/4B`, resolve it against a small seeded parcel registry, display the exact polygon, register the claim, and flag a second claim for the same parcel.
