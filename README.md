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
- Uploaded files are processed entirely in memory; nothing is written
  to disk during a normal request.
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
