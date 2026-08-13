# OCR Microservice

A standalone OCR microservice built with **FastAPI** and **PaddleOCR**.
It exposes a simple REST API for extracting text from
images and PDFs, and is designed to be deployed independently of any
main application, replaceable with a different OCR engine later without
touching the API contract.

---

## Features

- `POST /evaluate` compares OCR output with verified reference text
- Critical-field review signals distinguish model confidence from accuracy

- `POST /ocr` — extract text from an image or PDF
- `GET /health` — health check for uptime monitoring
- Original-color image handling tuned for PaddleOCR document detection
- Tamil/English recognition with configurable PaddleOCR model names
- Multi-page PDF support via Python-native PDFium
- Pluggable OCR engine interface (`OCRService`) — swap Tesseract for
  PaddleOCR, EasyOCR, or SuryaOCR without changing routes
- File validation: extension whitelist, size limit, MIME sniffing,
  and path-traversal-safe filenames
- Structured JSON responses and consistent error format
- Docker-ready, deployable to Render or Railway

---

## Project Structure

```
ocr-service/
├── app/
│   ├── api/
│   │   └── routes.py            # /health and /ocr endpoints
│   ├── services/
│   │   ├── image_processor.py   # OpenCV preprocessing pipeline
│   │   ├── pdf_processor.py     # PDF -> pages -> OCR -> merged text
│   │   └── ocr_engine.py        # OCRService interface + TesseractOCR
│   ├── models/
│   │   └── response_models.py   # Pydantic request/response models
│   ├── utils/
│   │   └── file_validation.py   # Extension/size/MIME/path validation
│   ├── config.py                # Environment-driven settings
│   └── main.py                  # FastAPI app + exception handlers
├── uploads/                      # Reserved for temp artifacts (unused by default)
├── Dockerfile
├── requirements.txt
├── README.md
└── .env
```

---

## Local Installation

### Prerequisites

- Python 3.11+
- Internet access on first run so PaddleOCR can download its English models

### Setup

```bash
git clone <your-repo-url> ocr-service
cd ocr-service

python3.11 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -r requirements.txt
cp .env .env.local             # optional: adjust values as needed
```

### Running locally

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The service will be available at `http://localhost:8000`.
Interactive API docs (Swagger UI) are auto-generated at
`http://localhost:8000/docs`.

---

## Docker Setup

### Build

```bash
docker build -t ocr-service .
```

### Run

```bash
docker run -p 8000:8000 --env-file .env ocr-service
```

The container installs the Python OCR and PDF runtimes, then starts
Uvicorn on port `8000`.

---

## API Documentation

### `GET /health`

Health check endpoint.

**Response — `200 OK`**

```json
{
  "status": "healthy"
}
```

---

### `POST /ocr`

Extracts text from an uploaded image or PDF.

- **Content-Type:** `multipart/form-data`
- **Field:** `file`
- **Supported formats:** jpg, jpeg, png, bmp, tif, tiff, pdf
- **Max size:** 10 MB

**Response — `200 OK`**

```json
{
  "success": true,
  "filename": "invoice.jpg",
  "processing_time": 1.42,
  "text": "...",
  "confidence": 92.4,
  "quality": {
    "model_confidence": 92.4,
    "confidence_is_text_accuracy": false,
    "requires_human_review": true,
    "review_reasons": [
      "Critical numeric or survey fields were detected and are not source-verified."
    ],
    "dates": ["03.02.2024"],
    "area_values": ["0.04.00"],
    "survey_fields": [
      {"identifier": "207/9", "value": "0.04.00", "source_verified": false}
    ],
    "mixed_script_tokens": []
  }
}
```

**Response — `400 Bad Request`** (unsupported type, empty file, file too
large, or corrupted/unreadable file)

```json
{
  "success": false,
  "message": "Unsupported file type."
}
```

**Response — `500 Internal Server Error`** (OCR engine failure)

```json
{
  "success": false,
  "message": "OCR processing failed."
}
```

---

### `POST /evaluate`

Submit `reference_text` and `ocr_text` as `multipart/form-data`. The
response reports character error rate (CER), word error rate (WER), and
exact-match accuracy for numeric fields, dates, survey numbers, and
survey/area pairs.

```json
{
  "character_error_rate": 0.08,
  "word_error_rate": 0.2,
  "numeric_field_accuracy": 60.0,
  "date_accuracy": 100.0,
  "survey_number_accuracy": 50.0,
  "critical_field_exact_match_accuracy": 0.0
}
```

`confidence` is average model confidence, not measured textual or factual
accuracy. Values under `quality` are review candidates and are not
automatically corrected or source-verified.

---

## Example curl Requests

### Structured land extraction

The base `POST /ocr` endpoint remains standalone. It never invokes land
enrichment and does not require an LLM API key.

To enrich OCR text that was extracted earlier:

```powershell
curl.exe -X POST "http://localhost:8000/land/extract" `
  -F "ocr_text=APK Minerals Pvt.Ltd 207/9 (0.04.00), 208/2B1 (0.08.50)"
```

To run OCR and optional enrichment in one request:

```powershell
curl.exe -X POST "http://localhost:8000/ocr/land" `
  -F "file=@C:\path\to\document.png"
```

Land results contain separate parcel records with holder, survey number,
area, explicit coordinates, administrative location, land type/use, document
references, and other evidence-backed attributes when available. Coordinates
are never estimated from place names or geocoded.

Every populated field contains the supporting OCR text, extraction method,
extractor confidence, and `source_verified: false`. Evidence proves only that
the value is supported by OCR output; legal, numeric, ownership, and location
values still require comparison with the source document.

Without `LLM_API_KEY`, deterministic survey, area, coordinate, date, reference,
and labeled location fields remain available. Holder and contextual inference
are omitted and the response status is `not_configured`.

**Health check:**

```bash
curl http://localhost:8000/health
```

**OCR on an image:**

```bash
curl -X POST http://localhost:8000/ocr \
  -F "file=@/path/to/invoice.jpg"
```

**OCR on a PDF:**

```bash
curl -X POST http://localhost:8000/ocr \
  -F "file=@/path/to/document.pdf"
```

---

## Configuration

All settings are environment-driven (see `.env`):

| Variable             | Default                                          | Description                          |
|----------------------|---------------------------------------------------|---------------------------------------|
| `MAX_FILE_SIZE_MB`   | `10`                                              | Maximum upload size in MB             |
| `ALLOWED_EXTENSIONS` | `["jpg","jpeg","png","bmp","tif","tiff","pdf"]`  | Accepted file extensions              |
| `MIN_IMAGE_WIDTH`    | `1200`                                            | Images narrower than this are upscaled|
| `PDF_DPI`            | `300`                                             | Rasterization DPI for PDF pages       |
| `PADDLEOCR_DETECTION_MODEL_NAME` | `PP-OCRv5_mobile_det`                  | Lightweight text detector             |
| `PADDLEOCR_RECOGNITION_MODEL_NAME` | `ta_PP-OCRv5_mobile_rec`             | Tamil/English recognition model       |
| `PADDLEOCR_DET_MODEL_DIR` | unset                                      | Optional local detection model path   |
| `PADDLEOCR_REC_MODEL_DIR` | unset                                      | Optional local recognition model path |

---

## Replacing the OCR Engine

The OCR engine is abstracted behind the `OCRService` interface in
`app/services/ocr_engine.py`:

```python
class OCRService(ABC):
    @abstractmethod
    def extract_text(self, image: np.ndarray) -> Tuple[str, float]:
        ...
```

To add a new engine (e.g. PaddleOCR), implement this interface in a new
class and swap the instantiation in `app/api/routes.py`. No route or
model changes are required.

---

## Security Notes

- Filenames are sanitized to remove path components and disallowed
  characters before being echoed back in responses.
- File content is sniffed (not just the extension) to confirm the real
  file type before processing.
- The standalone `/ocr` route processes uploads in memory. Authenticated
  `/api/pattas/process` uploads are retained in private local or S3 storage
  so their resulting claims remain auditable; they are never served publicly.
- Extracted text is never written to logs — only metadata (filename,
  size, type, timing, and errors) is logged.

---

## Deployment on Render

1. Push this repository to GitHub/GitLab.
2. In the Render dashboard, choose **New +** → **Web Service** and
   connect the repository.
3. Set **Environment** to **Docker** (Render will detect the
   `Dockerfile` automatically).
4. Set the **Port** to `8000` (matches the `EXPOSE` in the Dockerfile).
5. Add any environment variables from `.env` under the service's
   **Environment** tab.
6. Deploy. Render will build the image and expose a public URL you can
   call `POST https://<your-service>.onrender.com/ocr` against.

---

## Deployment on Railway

1. Push this repository to GitHub.
2. In Railway, choose **New Project** → **Deploy from GitHub repo**.
3. Railway will detect the `Dockerfile` and build automatically.
4. Under **Variables**, add any environment variables from `.env`.
5. Under **Settings**, confirm the service listens on port `8000`
   (Railway maps this to a public domain automatically).
6. Deploy. Your endpoint will be available at the generated
   `*.up.railway.app` domain.

---

## Notes on Extensibility

This service is intentionally decoupled from any main application: it
only communicates via HTTP, and its OCR engine can be replaced without
any changes to consumers of the `/ocr` endpoint. This makes it easy to
run as a shared internal service across multiple applications.

---

## Patta-to-parcel mapping and claims

The service now includes a central cadastral registry workflow while preserving `/ocr` as an independent endpoint:

```text
patta upload -> OCR -> deterministic parcel fields -> user confirmation
             -> registry lookup -> polygon map -> claim -> conflict review
```

A parcel is a geographic registry boundary. A claim is a user's assertion that their document relates to that parcel. Uploading a patta never changes registered ownership, and a conflict never decides ownership.

### Local PostGIS setup

1. Copy `.env.example` to `.env`, replace the database password and `AUTH_SECRET`, and keep `.env` out of version control.
2. Start PostgreSQL/PostGIS and ClamAV with `docker compose up -d db clamav`.
3. Install development dependencies with `python -m pip install -r requirements-dev.txt`.
4. Apply the schema with `alembic upgrade head`.
5. Import development aliases and parcels:

   ```text
   python -m scripts.import_aliases data/administrative_aliases.json
   python -m scripts.import_parcels data/synthetic_example_village.geojson
   ```

   The included 50-parcel dataset is synthetic, development-only, and explicitly non-authoritative. The acceptance parcel is Example Village survey `701`, subdivision `4B`.

6. Create development users and tokens:

   ```text
   python -m scripts.create_user alice --display-name Alice
   python -m scripts.create_user admin --display-name Administrator --role admin
   python -m scripts.mint_dev_token alice
   ```

7. Start with `uvicorn app.main:app --reload`, open `http://localhost:8000/land-mapping`, and paste the signed token into the access-token field.

For a lightweight SQLite demonstration, set `DATABASE_URL=sqlite+pysqlite:///./ocr_land.db`, set `MALWARE_SCAN_REQUIRED=false`, and run the same migration/import commands. SQLite is not the production spatial database.

### Land API

All `/api` routes require `Authorization: Bearer <signed-JWT>`. Upload and claim writes also require `Idempotency-Key`.

| Route | Purpose |
|---|---|
| `POST /api/pattas/process` | Securely store, OCR, normalize, and attempt resolution; does not create a claim |
| `POST /api/parcels/resolve` | Resolve user-corrected fields and persist valid candidate IDs |
| `GET /api/parcels/{id}` | Privacy-safe metadata and GeoJSON polygon |
| `POST /api/claims` | Transactionally create an idempotent claim and conflicts |
| `GET /api/claims/mine` | Current user's claims only |
| `GET /api/notifications/mine` | Generic current-user review notifications |
| `GET /api/admin/conflicts` | Administrator conflict queue with evidence and boundaries |
| `GET /api/admin/conflicts/{id}` | Administrator conflict detail |
| `PATCH /api/admin/conflicts/{id}` | Record an audited review status, notes, and history |

JWT subjects must match a central `users.external_id`. Roles are read from the database, not trusted from token claims. Replace the local symmetric-token arrangement with the deployment's OIDC issuer/verifier at the authentication adapter boundary when integrating an identity provider.

### Cadastral imports

`scripts/import_parcels.py` accepts a GeoJSON `FeatureCollection`. It converts Polygon to MultiPolygon, rejects empty/non-polygon geometry, safely repairs valid polygonal results, normalizes lookup fields, and upserts on:

```text
state + district + taluk + village + survey_number + subdivision_number
```

The report contains inserted, updated, skipped, invalid, duplicate, and repaired counts. Re-importing unchanged data is idempotent. Preserve `source`, `source_version`, and `source_record_id` for every authoritative update.

### Matching and conflicts

- Survey/subdivision normalization accepts `701/4b`, `701 / 4 B`, and `701-4B` without treating them as area.
- Ambiguous OCR pairs such as `B/8` and `O/0` require confirmation; alternatives never silently replace evidence.
- Areas support square metres, hectares, acres, cents, and `H.A.SqM` notation such as `0.12.00`.
- Exact automatic matching requires the complete administrative and survey/subdivision key. Aliases are verified registry entries. Fuzzy spelling results are suggestions only.
- PostGIS conflict calculations use intersection geography area and compare overlap against the smaller parcel. Configurable square-metre and percentage thresholds suppress precision slivers.
- Normal claim responses contain generic conflict facts only. Claimant identity, documents, detailed evidence, and both boundaries are restricted to administrators.

### Configuration

In addition to the OCR settings above, land mapping uses:

| Variable | Default | Purpose |
|---|---:|---|
| `DATABASE_URL` | local SQLite | Use PostgreSQL/PostGIS in production |
| `AUTH_SECRET` | development placeholder | HS256 development token verifier secret |
| `AUTH_ISSUER`, `AUTH_AUDIENCE` | local registry/API values | Required signed-token scope |
| `SECURE_UPLOAD_DIR` | `private_uploads` | Non-public local object root |
| `UPLOAD_STORAGE_BACKEND` | `local` | `local` or `s3` |
| `S3_BUCKET`, `S3_PREFIX` | empty / `patta-documents` | Private encrypted object storage |
| `CLAMAV_HOST`, `CLAMAV_PORT` | empty / `3310` | Malware scanning daemon |
| `MALWARE_SCAN_REQUIRED` | `false` | Fail closed when scanning is unavailable |
| `AREA_TOLERANCE_PERCENT` | `10` | Registry/document area warning threshold |
| `OVERLAP_MIN_SQM` | `1` | Spatial sliver area threshold |
| `OVERLAP_MIN_PERCENT` | `1` | Spatial sliver percentage threshold |
| `AUTOMATIC_MATCH_CONFIDENCE` | `0.85` | Automatic match policy threshold |
| `RATE_LIMIT_REQUESTS` | `60` | Protected writes per identity/window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | In-process limiter window |

Terminate TLS at a trusted ingress, encrypt database/storage/backups at rest, and use managed secrets in production. For multiple API replicas, replace the in-process rate-limit store with a shared gateway/Redis policy.

### Tests and operations

Run all tests with `python -m pytest -q`. The suite covers existing OCR behavior plus normalization, aliases, area conversion, migration, GeoJSON idempotency, exact/fuzzy resolution, transaction rollback, exact/spatial/duplicate conflicts, thresholds, authorization, upload/claim idempotency, notifications, UI delivery, and privacy-safe responses.

See [operations](docs/OPERATIONS.md) for alerts and tested backup/restore procedures, and [privacy and retention](docs/PRIVACY_RETENTION.md) for the production policy baseline.
