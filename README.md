# AranyaSetu

Patta OCR, cadastral parcel matching, exclusive legacy land-claim registration, and a protected Forest Rights Act workflow foundation for authorized staff.

AranyaSetu accepts Tamil and English patta scans, extracts the parcel reference, asks staff to verify the result, locates the corresponding cadastral polygon, and records the accepted document-to-parcel link. Registered polygons remain visible in a searchable map ledger, and authorized staff can reopen the original patta that created each claim.

The legacy registry keeps its exclusive-land rule: exact parcel duplicates and material polygon overlaps are rejected before another legacy claim is stored. Native FRA claims use a separate right-aware policy because IFR, CR, and CFR rights can have different exclusivity and layering rules.

> [!IMPORTANT]
> This repository is a research prototype, not a legal land-ownership system. A claim records a patta-to-parcel association; it does not create, transfer, or certify ownership. The included cadastral data is synthetic and must never be presented as authoritative.

## What the application does

- Reads JPG, PNG, BMP, TIFF, and PDF documents with PaddleOCR.
- Supports Tamil and English recognition, including common Tamil patta table layouts.
- Extracts state, district, taluk, village, survey number, subdivision, and document area when evidence is present.
- Shows OCR evidence and requires staff verification before registration.
- Resolves parcels by the full administrative and survey key.
- Displays real GeoJSON parcel boundaries, including irregular polygons.
- Rejects an exact duplicate parcel or a materially overlapping claimed polygon.
- Persists registered claims and polygons in the database.
- Provides a searchable claimed-land index synchronized with the map.
- Opens the privately stored original patta from a registered claim.
- Records upload, correction, claim, rejection, and patta-view audit events.
- Keeps the standalone OCR and optional land-enrichment APIs available separately.
- Models FRA rights holders, Gram Sabhas, claim decisions, versioned geometries, evidence, and titles under `/api/fra/*`.
- Evaluates FRA overlaps by right type, creates explicitly supporting local satellite evidence, and returns versioned advisory DSS recommendations.
- Provides a protected Tamil Nadu-first `/fra` workspace with searchable archive review, synchronized Atlas filters and summaries, versioned asset observations, advisory referrals, and privacy-safe printable reports.
- Keeps OCR, entity extraction, and asset models behind replaceable versioned gateways so trained models can be attached later without changing the legal workflow.

## Staff workflow

```text
Sign in
  -> upload a patta
  -> OCR and deterministic field extraction
  -> verify or correct the extracted fields
  -> resolve the official cadastral parcel
  -> inspect the parcel polygon and area comparison
  -> confirm the document-to-parcel match
  -> pass the exclusive-land availability check
  -> register and persist the claim
```

After registration, the **Claimed land** view lists every stored claim on the left and all registered polygons on the right. Selecting a list item highlights and zooms to its polygon; selecting a polygon opens the corresponding list record. Clicking the active item or polygon again deselects it. Each selected record can open the original uploaded patta.

## How duplicate claims are prevented

AranyaSetu uses several layers of protection:

1. A database unique constraint allows only one claim for a parcel ID.
2. PostgreSQL uses a transaction-scoped advisory lock to serialize availability checks.
3. PostGIS calculates the intersection area between the candidate parcel and every active claimed parcel.
4. Configurable square-metre and percentage thresholds ignore insignificant geometry slivers.
5. A competing request returns `409 Conflict`, records an audit event, and does not create another claim.

SQLite uses Shapely for development-time overlap checks. PostgreSQL/PostGIS is required for production concurrency and spatial behavior.

## Technology

| Area | Implementation |
|---|---|
| Web application and API | FastAPI, Uvicorn |
| OCR | PaddleOCR / PaddlePaddle |
| Image and PDF processing | OpenCV, Pillow, PDFium |
| Database | PostgreSQL 16 with PostGIS; SQLite for lightweight development |
| Spatial processing | PostGIS, GeoAlchemy2, Shapely |
| Persistence | SQLAlchemy and Alembic |
| Private document storage | Local private volume or Amazon S3 |
| Malware scanning | ClamAV INSTREAM |
| Browser UI | Server-hosted HTML, CSS, JavaScript, and Leaflet |
| Optional text enrichment | OpenAI-compatible API client |

## Architecture

```text
Staff browser
  -> FastAPI session and registry routes
      -> private document storage
      -> PaddleOCR and deterministic patta extraction
      -> cadastral parcel resolver
      -> exclusive-claim availability gate
      -> SQLAlchemy -> PostgreSQL/PostGIS
      -> append-only audit events

Standalone API clients
  -> OCR, evaluation, and optional enrichment routes

Authorized FRA clients
  -> protected FRA claim, evidence, spatial, satellite, and DSS routes
      -> append-only decisions and versioned geometries/titles
      -> right-aware PostGIS/Shapely spatial policy
      -> local replaceable provider interfaces
```

The browser UI never stores or displays its signed session token. Parcel responses expose registry geometry and provenance without claimant identifiers or private storage keys. Original pattas are streamed only through an authenticated, audited endpoint.

## Repository layout

```text
app/
  api/                    HTTP routes, authentication, and registry APIs
  db/                     SQLAlchemy models and session management
  models/                 API request and response models
  services/               OCR, extraction, matching, claims, storage, and audit logic
  static/login/           Temporary staff sign-in page
  static/land-mapping/    Upload, verification, registration, and claimed-land UI
  utils/                  Upload validation helpers
data/
  administrative_aliases.json
  demo_dss_rules.json
  synthetic_tamil_nadu_fra_archive.json
  synthetic_tamil_nadu_fra_atlas.geojson
  synthetic_example_village.geojson
docs/                     Operations, privacy, specifications, and implementation plans
migrations/               Alembic database migrations
scripts/                  Import, user, token, backup, and restore commands
tests/                    Python and browser-logic test suites
docker-compose.yml        API, PostGIS, and ClamAV development stack
Dockerfile                Production-shaped API image
```

## Quick start with Docker Compose

### Prerequisites

- Docker Desktop or Docker Engine with Compose
- At least several gigabytes of free disk space for OCR models and container images
- Internet access during the first build and first OCR model download

### 1. Clone the repository

```bash
git clone https://github.com/vTg2208/ocr-service.git
cd ocr-service
```

### 2. Create the environment file

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

Generate an authentication secret:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Place the generated value in `.env` as `AUTH_SECRET`. The sample Compose database uses the password `change-me`; if you change it, update both `DATABASE_URL` and the database service password in `docker-compose.yml`.

### 3. Build and start the services

```bash
docker compose up -d --build
docker compose ps
```

The API container waits for PostGIS and ClamAV, applies Alembic migrations, and then starts Uvicorn on port `8000`.

### 4. Import development reference data

```bash
docker compose exec api python -m scripts.import_aliases data/administrative_aliases.json
docker compose exec api python -m scripts.import_parcels data/synthetic_example_village.geojson
```

The GeoJSON file contains 52 synthetic parcels, including irregular shapes used to demonstrate realistic boundaries. Imports are idempotent and report inserted, updated, skipped, invalid, duplicate, and repaired records.

### 5. Open the application

- Staff login: <http://localhost:8000/login>
- Tamil Nadu FRA workspace: <http://localhost:8000/fra>
- API documentation: <http://localhost:8000/docs>
- Liveness: <http://localhost:8000/health>
- Database readiness: <http://localhost:8000/health/ready>

For the local sample configuration, sign in with access code `1234` or the value assigned to `DEMO_ACCESS_CODE`.

Seed the complete invented Tamil Nadu FRA story after migrations:

```bash
docker compose exec api python -m scripts.seed_tamil_nadu_fra_demo
docker compose exec api python -m scripts.run_fra_jobs --max-jobs 20
```

The seed is idempotent and includes three synthetic village profiles, IFR/CR/CFR archive examples, claims and versioned geometry, a synthetic title, time-separated supporting observations, and advisory rule/referral examples. It is not authoritative case data. Trained models are optional and can be attached later using [the model adapter guide](docs/MODEL_ADAPTERS.md).

### Stop the stack

```bash
docker compose down
```

Named volumes preserve the database, private uploads, ClamAV definitions, and downloaded Paddle models. `docker compose down -v` also deletes those volumes and their data; use it only when a full reset is intended.

## Local development without the full stack

### Prerequisites

- Python 3.11
- A C/C++ runtime supported by PaddlePaddle
- Node.js 18 or newer only for the browser-logic tests
- PostgreSQL/PostGIS for production-like spatial testing, or SQLite for lightweight local testing

Create and activate a virtual environment:

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements-dev.txt
```

PowerShell activation:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

For a SQLite development run, create `.env` with at least:

```dotenv
ENVIRONMENT=development
DATABASE_URL=sqlite+pysqlite:///./ocr_land.db
AUTH_SECRET=replace-with-a-long-random-development-secret
DEMO_AUTH_ENABLED=true
DEMO_ACCESS_CODE=1234
DEMO_SESSION_MINUTES=480
SECURE_UPLOAD_DIR=private_uploads
MALWARE_SCAN_REQUIRED=false
CLAMAV_HOST=
```

Apply the schema, import the synthetic registry, and start the server:

```bash
alembic upgrade head
python -m scripts.import_aliases data/administrative_aliases.json
python -m scripts.import_parcels data/synthetic_example_village.geojson
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The first OCR request can take longer because PaddleOCR may download and initialize its models.

## Authentication

### Temporary browser login

`POST /api/auth/demo-login` validates the configured four-digit access code, creates or updates the `registry-demo` database user, and returns an HttpOnly session cookie. The cookie is `SameSite=Strict` and becomes `Secure` when `ENVIRONMENT=production`.

The access-code authentication is intentionally temporary. Before a real deployment:

- disable `DEMO_AUTH_ENABLED`;
- replace the local HS256 adapter with the authority's OIDC verifier;
- provision staff identities and roles from the trusted identity system;
- rotate `AUTH_SECRET` and invalidate temporary sessions.

### Bearer-token development access

Protected APIs also accept `Authorization: Bearer <JWT>`. The JWT subject must match a row in `users.external_id`; roles are read from the database and are not trusted from token claims.

```bash
python -m scripts.create_user alice --display-name "Alice" --role user
python -m scripts.mint_dev_token alice --minutes 60
```

These scripts are for local development, not production identity management.

## API overview

### Public OCR and enrichment routes

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/health` | Process liveness check |
| `GET` | `/health/ready` | Database readiness check |
| `POST` | `/ocr` | OCR for an image or PDF; optional prompt-based analysis |
| `POST` | `/evaluate` | Compare OCR text with verified reference text |
| `POST` | `/land/extract` | Extract evidence-backed land records from existing OCR text |
| `POST` | `/ocr/land` | Run OCR and optional land-record enrichment together |

The base `/ocr` route does not require an LLM key. It returns extracted text, average model confidence, and review signals for dates, areas, survey references, and mixed-script tokens. Model confidence is not measured textual or factual accuracy.

### Browser and session routes

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/` | Redirect to staff login |
| `GET` | `/login` | Temporary staff sign-in page |
| `GET` | `/land-mapping` | Staff claim application |
| `POST` | `/api/auth/demo-login` | Start a temporary staff session |
| `GET` | `/api/auth/session` | Return the signed-in staff identity |
| `POST` | `/api/auth/logout` | Clear the browser session |

### Protected registry routes

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/pattas/process` | Validate, scan, privately store, OCR, extract, and attempt parcel resolution |
| `POST` | `/api/parcels/resolve` | Resolve staff-corrected fields and persist valid candidate IDs |
| `GET` | `/api/parcels/{parcel_id}` | Return privacy-safe parcel metadata and GeoJSON geometry |
| `POST` | `/api/claims` | Register an available parcel or return `409` when land is already claimed |
| `GET` | `/api/claims/registry` | Return persistent claimed polygons, summaries, and patta view URLs |
| `GET` | `/api/claims/{claim_id}/patta` | Stream the authenticated claim's original patta |
| `GET` | `/api/claims/mine` | Return the current user's claims |
| `GET` | `/api/notifications/mine` | Return the current user's notifications |

`POST /api/pattas/process` and `POST /api/claims` require an `Idempotency-Key` header. Repeating a successful request with the same user and key returns the existing result instead of creating a duplicate.

### Legacy conflict-review routes

| Method | Route | Access |
|---|---|---|
| `GET` | `/api/admin/conflicts` | Administrator |
| `GET` | `/api/admin/conflicts/{conflict_id}` | Administrator |
| `PATCH` | `/api/admin/conflicts/{conflict_id}` | Administrator |

These routes support historical conflict records. New competing claims are rejected by the exclusive-claim gate instead of creating a second claim and conflict record.

### Protected FRA foundation routes

The `/api/fra/*` domain covers rights holders, Gram Sabhas, IFR/CR/CFR claims, versioned geometry and evidence, reviewer-controlled transitions and titles, legacy promotion, right-aware spatial evaluation, supporting satellite observations, and explainable DSS recommendations. It is backward-compatible with the legacy routes above.

Satellite observations are supporting evidence and do not determine legal validity. DSS recommendations are advisory and do not approve or sanction benefits. See the [FRA foundation guide](docs/FRA_FOUNDATION.md) for routes, roles, local manifests, sample rules, and limitations.

## API examples

### Standalone OCR

```bash
curl -X POST http://localhost:8000/ocr \
  -F "file=@/path/to/patta.png"
```

Supported extensions are `jpg`, `jpeg`, `png`, `bmp`, `tif`, `tiff`, and `pdf`. The default upload limit is 10 MB.

### OCR evaluation

```bash
curl -X POST http://localhost:8000/evaluate \
  -F "reference_text=Survey No. 614/1B" \
  -F "ocr_text=Survey No. 614/IB"
```

The response reports character error rate, word error rate, and exact-match accuracy for critical numeric, date, survey, and survey-area fields.

### Authenticated browser-style request

```bash
curl -c cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"access_code":"1234"}' \
  http://localhost:8000/api/auth/demo-login

curl -b cookies.txt \
  -H "Idempotency-Key: upload-example-1" \
  -F "file=@/path/to/patta.png" \
  http://localhost:8000/api/pattas/process
```

## OCR and parcel matching behavior

### Extraction

- Images narrower than `MIN_IMAGE_WIDTH` are enlarged before recognition.
- PDFs are rasterized at `PDF_DPI` and processed page by page.
- The default recognizer is the Tamil PP-OCRv5 mobile model.
- Tamil table extraction recognizes survey/subdivision rows such as `614` and `1B`, plus hectare-are extents such as `0 - 5.00`.
- `H.A.SqM`, hectares, acres, cents, and square metres are normalized to square metres.
- Ambiguous characters such as `B/8` and `O/0` remain alternatives requiring human confirmation.
- Every extracted field keeps its supporting OCR evidence when available.

### Parcel lookup

A survey number is not globally unique. Exact lookup requires:

```text
state + district + taluk + village + survey number + subdivision number
```

Verified administrative aliases are normalized before lookup. A close village spelling can be suggested, but it is never silently accepted. Area differences generate warnings and do not invent a parcel location.

### Cadastral boundaries

The importer accepts GeoJSON `Polygon` and `MultiPolygon` features, converts them to `MultiPolygon`, attempts safe repair of invalid polygonal geometry, and upserts on the full parcel key. Every authoritative record should retain `source`, `source_version`, and `source_record_id`.

Run an import with:

```bash
python -m scripts.import_parcels /path/to/parcels.geojson
```

## Configuration reference

Environment variables are loaded from `.env`. Environment values override application defaults.

### Core and OCR

| Variable | Application default | Purpose |
|---|---:|---|
| `APP_NAME` | `AranyaSetu` | FastAPI application name |
| `ENVIRONMENT` | `development` | Enables production safeguards when set to `production` |
| `LOG_LEVEL` | `INFO` | Application log level |
| `MAX_FILE_SIZE_MB` | `10` | Maximum upload size |
| `ALLOWED_EXTENSIONS` | JPG, PNG, BMP, TIFF, PDF | Accepted upload extensions |
| `MIN_IMAGE_WIDTH` | `1200` | Width below which images are enlarged |
| `PDF_DPI` | `300` | PDF rasterization resolution |
| `PADDLEOCR_DETECTION_MODEL_NAME` | `PP-OCRv5_mobile_det` | Text detection model |
| `PADDLEOCR_RECOGNITION_MODEL_NAME` | `ta_PP-OCRv5_mobile_rec` | Text recognition model |
| `PADDLEOCR_DET_MODEL_DIR` | unset | Optional local detection model directory |
| `PADDLEOCR_REC_MODEL_DIR` | unset | Optional local recognition model directory |

### Registry, authentication, and storage

| Variable | Application default | Purpose |
|---|---:|---|
| `DATABASE_URL` | SQLite database in the project directory | SQLAlchemy database URL |
| `AUTH_SECRET` | insecure development placeholder | HS256 development/session signing secret |
| `AUTH_ISSUER` | `ocr-land-registry` | Required token issuer |
| `AUTH_AUDIENCE` | `ocr-land-api` | Required token audience |
| `DEMO_AUTH_ENABLED` | `true` | Enable the temporary access-code login |
| `DEMO_ACCESS_CODE` | `1234` | Temporary local access code |
| `DEMO_SESSION_MINUTES` | `480` | Temporary session lifetime |
| `SECURE_UPLOAD_DIR` | `private_uploads` | Local private document root |
| `UPLOAD_STORAGE_BACKEND` | `local` | `local` or `s3` |
| `S3_BUCKET` | empty | Required for S3 storage |
| `S3_PREFIX` | `patta-documents` | Private S3 object prefix |
| `CLAMAV_HOST` | empty | ClamAV host; omitted scanning is allowed only outside fail-closed mode |
| `CLAMAV_PORT` | `3310` | ClamAV daemon port |
| `MALWARE_SCAN_REQUIRED` | `false` | Reject uploads if scanning is unavailable |

### Matching, overlap, limits, and optional enrichment

| Variable | Application default | Purpose |
|---|---:|---|
| `AREA_TOLERANCE_PERCENT` | `10` | Warn when document and official area differ beyond this percentage |
| `AUTOMATIC_MATCH_CONFIDENCE` | `0.85` | Minimum confidence for an automatic match |
| `OVERLAP_MIN_SQM` | `1` | Minimum intersection area treated as a conflict |
| `OVERLAP_MIN_PERCENT` | `1` | Minimum intersection percentage of the smaller parcel |
| `RATE_LIMIT_REQUESTS` | `60` | Protected requests allowed per identity/window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | In-process rate-limit window |
| `LLM_API_KEY` | empty | Enables optional prompt and contextual enrichment |
| `LLM_BASE_URL` | Groq OpenAI-compatible endpoint | OpenAI-compatible API base URL |
| `LLM_MODEL_NAME` | `openai/gpt-oss-120b` | Provider model identifier |

When `ENVIRONMENT=production`, startup rejects a default/short `AUTH_SECRET`, SQLite, or disabled fail-closed malware scanning.

## Testing

Run the complete Python suite:

```bash
python -m pytest -q
```

Run the dependency-free browser-logic suite:

```bash
node --test tests/land_mapping_ui.test.js
```

Useful focused checks:

```bash
python -m pytest -q tests/test_patta_extraction.py tests/test_parcel_resolver.py
python -m pytest -q tests/test_claim_eligibility.py tests/test_claim_service.py
python -m pytest -q tests/test_land_api.py tests/test_land_mapping_ui.py
python -m pytest -q tests/test_migrations.py tests/test_production_safeguards.py
```

## Security and privacy boundaries

- Uploaded registry documents are stored outside the public static directory.
- Local paths are resolved beneath the configured private root; S3 keys are constrained to the configured prefix.
- Patta responses are authenticated, use `Cache-Control: private, no-store`, and are audited.
- File extension, MIME signature, size, filename, and decodability are validated.
- Production uploads fail closed when ClamAV is unavailable.
- Registry responses exclude claimant IDs and private storage keys.
- JWT issuer, audience, timestamps, signature, and database-backed subject are validated.
- Database roles, not token role claims, determine administrator access.
- Request IDs are returned and written to metadata-only access logs.
- Raw OCR text, document content, access tokens, and signed storage URLs must not be logged.

See [Privacy and retention](docs/PRIVACY_RETENTION.md) for the baseline data policy.

## Production checklist

Before using this system with real records:

- Replace the temporary access code with an approved OIDC identity provider.
- Use PostgreSQL/PostGIS and apply all Alembic migrations.
- Import licensed, authoritative cadastral boundaries with provenance.
- Remove synthetic parcels from the user-facing database.
- Enable fail-closed malware scanning.
- Use managed secrets and rotate all temporary credentials.
- Terminate TLS at a trusted ingress.
- Encrypt database, object storage, and backups at rest.
- Use private S3 or an equivalently controlled document store.
- Replace the in-process rate limiter when running multiple API replicas.
- Establish the approved retention period, legal hold, access/export, and erasure processes.
- Test backup restoration in an isolated environment.
- Obtain security, privacy, accessibility, and legal review.

See [Operations](docs/OPERATIONS.md) for monitoring, backup, restore, import, and incident-response guidance.

## Troubleshooting

### The API exits during startup

With `ENVIRONMENT=production`, verify that `AUTH_SECRET` contains at least 32 non-default characters, `DATABASE_URL` points to PostgreSQL/PostGIS, and `MALWARE_SCAN_REQUIRED=true`.

### OCR initialization fails or the first request is slow

Confirm that PaddlePaddle is supported on the host and that the container can download the configured models. Docker Compose preserves downloads in the `paddle_models` volume. Local model directories can be supplied with `PADDLEOCR_DET_MODEL_DIR` and `PADDLEOCR_REC_MODEL_DIR`.

### Extracted fields remain blank

OCR confidence does not guarantee that the required parcel fields were recognized. Check image clarity and rotation, then use **Show OCR sources** and correct the fields manually. Parcel resolution still requires the full administrative and survey key.

### The parcel is not found in the registry

Confirm that reference data was imported and that state, district, taluk, village, survey number, and subdivision match the registry record. The application does not geocode a place name or invent a polygon when the cadastral record is absent.

### Claim registration returns `409`

This is expected when the parcel ID is already claimed or its polygon materially overlaps active claimed land. Inspect the existing parcel in the **Claimed land** view instead of creating another claim.

### A stored patta cannot be opened

Check that the private upload volume or S3 object still exists and that the current process uses the same `SECURE_UPLOAD_DIR` or S3 configuration used when the document was registered.

## Additional documentation

- [Operations guide](docs/OPERATIONS.md)
- [Privacy and retention baseline](docs/PRIVACY_RETENTION.md)
- [Forest Rights Act foundation](docs/FRA_FOUNDATION.md)
- [Exclusive land-claims design](docs/superpowers/specs/2026-08-26-exclusive-land-claims-design.md)
- [Exclusive land-claims implementation plan](docs/superpowers/plans/2026-08-26-exclusive-land-claims.md)

## License

No license file is currently included. Treat the repository as all rights reserved until the project owner adds an explicit license.
