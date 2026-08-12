# Land Record Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional evidence-backed, separate-parcel land-record extraction on top of the independently functioning OCR service.

**Architecture:** Keep `/ocr` unchanged and add a deterministic candidate extractor, an optional constrained LLM enrichment service, and strict post-LLM evidence validation. Expose enrichment through `/land/extract` and an isolated convenience endpoint `/ocr/land`; enrichment errors never change a successful base OCR result.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, PaddleOCR, OpenAI-compatible `AsyncOpenAI`, `unittest`.

## Global Constraints

- `POST /ocr` remains independent and never invokes land enrichment.
- Raw OCR text is never modified by enrichment.
- Each survey parcel is returned as a separate land record.
- Coordinates are returned only when explicitly present in OCR text.
- Every populated inferred field includes exact OCR evidence.
- Unsupported LLM values are rejected rather than guessed.
- Automated fields always use `source_verified: false`.
- Land endpoints return deterministic partial results without an LLM key.
- Raw OCR text and inferred holder details are not logged.
- This directory has no `.git` metadata, so task checkpoints cannot be committed unless the user initializes a repository first.

---

## File Structure

- Create `app/services/land_candidates.py`: deterministic candidate discovery only.
- Create `app/services/land_enrichment.py`: record assembly, LLM invocation, and evidence validation.
- Create `app/api/land_routes.py`: additive land endpoints and failure isolation.
- Modify `app/models/response_models.py`: public land/evidence response contracts.
- Modify `app/main.py`: register the additive land router.
- Modify `README.md`: endpoint and response documentation.
- Create `tests/test_land_candidates.py`: numeric, area, coordinate, and location candidate tests.
- Create `tests/test_land_enrichment.py`: parcel grouping, evidence validation, and no-LLM behavior.
- Create `tests/test_land_routes.py`: endpoint behavior and OCR independence tests.

### Task 1: Public Land-Record Response Models

**Files:**
- Modify: `app/models/response_models.py`
- Create: `tests/test_land_models.py`

**Interfaces:**
- Produces: `FieldEvidence`, `EvidencedText`, `AreaField`, `CoordinateField`, `LandLocation`, `DocumentReference`, `OtherLandAttribute`, `LandRecord`, `LandExtractionResult`, and `OCRLandResponse`.
- Consumes: existing `OCRResponse` for `OCRLandResponse.ocr`.

- [ ] **Step 1: Write the failing model-contract test**

```python
import unittest

from app.models.response_models import (
    AreaField,
    EvidencedText,
    FieldEvidence,
    LandExtractionResult,
    LandRecord,
)


class LandModelTests(unittest.TestCase):
    def test_land_record_serializes_evidence_and_missing_coordinates(self):
        evidence = FieldEvidence(
            text="207/9 (0.04.00)",
            method="deterministic",
            confidence=1.0,
        )
        record = LandRecord(
            record_id="land-1",
            survey_number=EvidencedText(value="207/9", evidence=evidence),
            area=AreaField(
                raw_value="0.04.00",
                unit=None,
                normalized_square_metres=None,
                evidence=evidence,
            ),
        )
        result = LandExtractionResult(
            status="partial",
            records=[record],
            record_count=1,
            requires_human_review=True,
            warnings=["Source verification required."],
        )

        payload = result.model_dump()
        self.assertIsNone(payload["records"][0]["latitude"])
        self.assertFalse(
            payload["records"][0]["survey_number"]["evidence"]["source_verified"]
        )
```

- [ ] **Step 2: Run the test and confirm the missing-model failure**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_models -v`

Expected: import failure because the land response models do not exist.

- [ ] **Step 3: Add the response models with strict field bounds**

```python
from typing import Literal


class FieldEvidence(BaseModel):
    text: str
    method: Literal["deterministic", "llm_with_ocr_evidence", "hybrid"]
    confidence: float = Field(..., ge=0, le=1)
    source_verified: bool = False


class EvidencedText(BaseModel):
    value: str
    evidence: FieldEvidence


class AreaField(BaseModel):
    raw_value: str
    unit: str | None = None
    normalized_square_metres: float | None = Field(default=None, ge=0)
    evidence: FieldEvidence


class CoordinateField(BaseModel):
    value: float
    format: Literal["decimal", "dms"]
    evidence: FieldEvidence


class LandLocation(BaseModel):
    village: EvidencedText | None = None
    taluk: EvidencedText | None = None
    district: EvidencedText | None = None
    state: EvidencedText | None = None
    address: EvidencedText | None = None


class DocumentReference(BaseModel):
    kind: str
    value: str
    evidence: FieldEvidence


class OtherLandAttribute(BaseModel):
    key: str
    value: str
    evidence: FieldEvidence


class LandRecord(BaseModel):
    record_id: str
    holder: EvidencedText | None = None
    holder_type: Literal["person", "organization", "unknown"] | None = None
    survey_number: EvidencedText | None = None
    area: AreaField | None = None
    latitude: CoordinateField | None = None
    longitude: CoordinateField | None = None
    location: LandLocation = Field(default_factory=LandLocation)
    land_type: EvidencedText | None = None
    uses_or_resources: list[EvidencedText] = Field(default_factory=list)
    document_references: list[DocumentReference] = Field(default_factory=list)
    other_attributes: list[OtherLandAttribute] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LandExtractionResult(BaseModel):
    status: Literal["completed", "partial", "not_configured", "failed"]
    records: list[LandRecord] = Field(default_factory=list)
    record_count: int = Field(..., ge=0)
    requires_human_review: bool
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class OCRLandResponse(BaseModel):
    ocr: OCRResponse
    land_extraction: LandExtractionResult
```

- [ ] **Step 4: Run the model test**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_models -v`

Expected: PASS.

- [ ] **Step 5: Record the task checkpoint**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_models -v`

Expected: PASS. No commit is possible unless Git is initialized.

### Task 2: Deterministic Land Candidate Extraction

**Files:**
- Create: `app/services/land_candidates.py`
- Create: `tests/test_land_candidates.py`

**Interfaces:**
- Produces: `TextCandidate`, `SurveyAreaCandidate`, `CoordinatePairCandidate`, `LocationCandidate`, `LandCandidateSet`, and `extract_land_candidates(text: str) -> LandCandidateSet`.
- Consumes: raw OCR text only; no model or LLM dependencies.

- [ ] **Step 1: Write failing tests for separate parcels and explicit coordinates**

```python
import unittest

from app.services.land_candidates import extract_land_candidates


class LandCandidateTests(unittest.TestCase):
    def test_extracts_separate_survey_area_pairs(self):
        candidates = extract_land_candidates(
            "207/9 (0.04.00), 208/2B1 (0.08.50), total area 0.12.50 hectares"
        )
        self.assertEqual(
            [(item.survey_number, item.area_raw) for item in candidates.parcels],
            [("207/9", "0.04.00"), ("208/2B1", "0.08.50")],
        )

    def test_extracts_only_explicit_valid_coordinate_pairs(self):
        candidates = extract_land_candidates(
            "Latitude: 12.6934 Longitude: 79.9757, Kanchipuram District"
        )
        self.assertEqual(len(candidates.coordinates), 1)
        self.assertEqual(candidates.coordinates[0].latitude, 12.6934)
        self.assertEqual(candidates.coordinates[0].longitude, 79.9757)
        self.assertEqual(extract_land_candidates("Kanchipuram District").coordinates, [])
        self.assertEqual(
            extract_land_candidates("Latitude: 120 Longitude: 300").coordinates,
            [],
        )

    def test_extracts_administrative_locations_dates_and_references(self):
        candidates = extract_land_candidates(
            "Pazhaveri Village, Uthiramerur Taluk, Kanchipuram District, "
            "Ref No.346/Q3/2022 dated 03.02.2024"
        )
        self.assertEqual(
            [(item.kind, item.value) for item in candidates.locations],
            [
                ("village", "Pazhaveri"),
                ("taluk", "Uthiramerur"),
                ("district", "Kanchipuram"),
            ],
        )
        self.assertEqual([item.value for item in candidates.dates], ["03.02.2024"])
        self.assertEqual(
            [item.value for item in candidates.reference_numbers],
            ["346/Q3/2022"],
        )
```

- [ ] **Step 2: Write a failing DMS-coordinate test**

```python
class DMSCoordinateTests(unittest.TestCase):
    def test_converts_explicit_dms_coordinates(self):
        candidates = extract_land_candidates(
            '12°41\'36.2"N 79°58\'32.5"E'
        )
        coordinate = candidates.coordinates[0]
        self.assertEqual(round(coordinate.latitude, 5), 12.69339)
        self.assertEqual(round(coordinate.longitude, 5), 79.97569)
        self.assertEqual(coordinate.format, "dms")
```

- [ ] **Step 3: Run candidate tests and confirm import failure**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_candidates -v`

Expected: import failure for `app.services.land_candidates`.

- [ ] **Step 4: Implement focused candidate dataclasses and regex extraction**

```python
@dataclass(frozen=True)
class TextCandidate:
    value: str
    evidence_text: str
    start: int
    end: int


@dataclass(frozen=True)
class SurveyAreaCandidate:
    survey_number: str
    area_raw: str | None
    unit: str | None
    normalized_square_metres: float | None
    evidence_text: str
    start: int
    end: int


@dataclass(frozen=True)
class CoordinatePairCandidate:
    latitude: float
    longitude: float
    format: Literal["decimal", "dms"]
    evidence_text: str
    start: int
    end: int


@dataclass(frozen=True)
class LocationCandidate:
    kind: Literal["village", "taluk", "district", "state", "address"]
    value: str
    evidence_text: str
    start: int
    end: int


@dataclass
class LandCandidateSet:
    parcels: list[SurveyAreaCandidate] = field(default_factory=list)
    coordinates: list[CoordinatePairCandidate] = field(default_factory=list)
    dates: list[TextCandidate] = field(default_factory=list)
    reference_numbers: list[TextCandidate] = field(default_factory=list)
    locations: list[LocationCandidate] = field(default_factory=list)
```

Implement `extract_land_candidates` using named regex groups. Preserve the
exact matched substring as `evidence_text`. Validate coordinate ranges after
parsing. Convert DMS with:

```python
def _dms_to_decimal(degrees, minutes, seconds, direction):
    value = degrees + minutes / 60 + seconds / 3600
    return -value if direction.upper() in {"S", "W"} else value
```

Only normalize area when an explicit recognized unit makes the conversion
deterministic; otherwise set `normalized_square_metres=None`.

Recognize English administrative suffixes (`Village`, `Taluk`, `District`,
`State`) case-insensitively and retain the full labeled phrase as evidence.
Tamil administrative labels may be added only with explicit label/value regex
pairs; do not infer location hierarchy from unlabeled words.

- [ ] **Step 5: Run candidate tests**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_candidates -v`

Expected: PASS for parcel separation, no-coordinate inference, range checks,
and DMS conversion.

- [ ] **Step 6: Record the task checkpoint**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_candidates -v`

Expected: PASS. No commit is possible unless Git is initialized.

### Task 3: Deterministic Separate-Record Assembly

**Files:**
- Create: `app/services/land_enrichment.py`
- Create: `tests/test_land_enrichment.py`

**Interfaces:**
- Consumes: `extract_land_candidates(text) -> LandCandidateSet` and Task 1 response models.
- Produces: `build_deterministic_land_result(text: str) -> tuple[LandExtractionResult, LandCandidateSet]`.

- [ ] **Step 1: Write a failing test for one record per parcel**

```python
import unittest

from app.services.land_enrichment import build_deterministic_land_result


class DeterministicLandEnrichmentTests(unittest.TestCase):
    def test_builds_one_unverified_record_per_parcel(self):
        result, candidates = build_deterministic_land_result(
            "207/9 (0.04.00), 208/2B1 (0.08.50)"
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.record_count, 2)
        self.assertEqual(
            [record.record_id for record in result.records],
            ["land-1", "land-2"],
        )
        self.assertEqual(
            [record.survey_number.value for record in result.records],
            ["207/9", "208/2B1"],
        )
        self.assertTrue(
            all(not record.area.evidence.source_verified for record in result.records)
        )
        self.assertTrue(result.requires_human_review)
```

- [ ] **Step 2: Write a failing conservative-coordinate association test**

```python
class CoordinateAssociationTests(unittest.TestCase):
    def test_attaches_one_explicit_coordinate_pair_only_to_single_parcel(self):
        result, _ = build_deterministic_land_result(
            "207/9 (0.04.00) Latitude: 12.6934 Longitude: 79.9757"
        )
        self.assertEqual(result.records[0].latitude.value, 12.6934)
        self.assertEqual(result.records[0].longitude.value, 79.9757)

        ambiguous, _ = build_deterministic_land_result(
            "207/9 (0.04.00), 208/2B1 (0.08.50) "
            "Latitude: 12.6934 Longitude: 79.9757"
        )
        self.assertTrue(all(record.latitude is None for record in ambiguous.records))
        self.assertTrue(
            any("coordinate" in warning.lower() for warning in ambiguous.warnings)
        )
```

- [ ] **Step 3: Run enrichment tests and confirm missing-function failure**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_enrichment -v`

Expected: import or attribute failure for `build_deterministic_land_result`.

- [ ] **Step 4: Implement deterministic record assembly**

Create exact `FieldEvidence(method="deterministic", confidence=1.0)` objects
from candidate evidence. Assign `land-1`, `land-2`, and so on in OCR reading
order. Set status to `partial`, require review whenever records or coordinates
exist, and include a warning that legal/numeric values are not source-verified.

Attach one coordinate pair only when exactly one parcel exists. When multiple
parcels share an unscoped coordinate pair, leave parcel coordinates null and
add an ambiguity warning.

- [ ] **Step 5: Run deterministic enrichment tests**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_enrichment -v`

Expected: PASS.

- [ ] **Step 6: Record the task checkpoint**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_candidates tests.test_land_enrichment -v`

Expected: PASS. No commit is possible unless Git is initialized.

### Task 4: Optional LLM Grouping and Evidence Validation

**Files:**
- Modify: `app/services/land_enrichment.py`
- Modify: `tests/test_land_enrichment.py`

**Interfaces:**
- Consumes: raw OCR text, `LandCandidateSet`, deterministic records, and an injected OpenAI-compatible async client.
- Produces: `LandEnrichmentService.extract(text: str) -> LandExtractionResult`.

- [ ] **Step 1: Write a failing no-LLM fallback test**

```python
import unittest


class LandEnrichmentFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_llm_returns_deterministic_not_configured_result(self):
        service = LandEnrichmentService(client=None, model_name="unused")
        result = await service.extract("207/9 (0.04.00)")
        self.assertEqual(result.status, "not_configured")
        self.assertIsNone(result.records[0].holder)
        self.assertEqual(result.records[0].survey_number.value, "207/9")
```

- [ ] **Step 2: Write a failing evidence-validation test with a fake client**

```python
import json
from types import SimpleNamespace


def fake_llm_payload():
    return {
        "records": [
            {
                "survey_number": "207/9",
                "holder": {
                    "value": "APK Minerals Pvt.Ltd",
                    "evidence_text": "APK Minerals Pvt.Ltd",
                    "confidence": 0.94,
                },
                "location": {
                    "district": {
                        "value": "Kanchipuram",
                        "evidence_text": "Kanchipuram District",
                        "confidence": 0.9,
                    }
                },
            }
        ]
    }


class FakeCompletions:
    def __init__(self, payload):
        self.payload = payload

    async def create(self, **kwargs):
        message = SimpleNamespace(content=json.dumps(self.payload))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, payload):
        self.chat = SimpleNamespace(completions=FakeCompletions(payload))


class LandEvidenceValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_hallucinated_holder_and_numeric_values(self):
        text = "207/9 (0.04.00), Kanchipuram District"
        payload = fake_llm_payload()
        payload["records"][0]["holder"] = {
            "value": "Invented Owner",
            "evidence_text": "Invented Owner",
            "confidence": 0.99,
        }
        payload["records"][0]["area"] = {
            "raw_value": "9.99.99",
            "evidence_text": "207/9 (0.04.00)",
            "confidence": 0.99,
        }
        service = LandEnrichmentService(
            client=FakeClient(payload),
            model_name="test-model",
        )
        result = await service.extract(text)
        self.assertIsNone(result.records[0].holder)
        self.assertEqual(result.records[0].area.raw_value, "0.04.00")
        self.assertTrue(
            any("rejected" in warning.lower() for warning in result.warnings)
        )
```

- [ ] **Step 3: Write a failing supported-holder grouping test**

```python
class SharedHolderGroupingTests(unittest.IsolatedAsyncioTestCase):
    async def test_adds_supported_holder_to_each_llm_grouped_parcel(self):
        text = (
            "APK Minerals Pvt.Ltd applied for "
            "207/9 (0.04.00), 208/2B1 (0.08.50)"
        )
        payload = {
            "records": [
                {
                    "survey_number": "207/9",
                    "holder": {
                        "value": "APK Minerals Pvt.Ltd",
                        "evidence_text": "APK Minerals Pvt.Ltd",
                        "confidence": 0.94,
                    },
                },
                {
                    "survey_number": "208/2B1",
                    "holder": {
                        "value": "APK Minerals Pvt.Ltd",
                        "evidence_text": "APK Minerals Pvt.Ltd",
                        "confidence": 0.94,
                    },
                },
            ]
        }
        service = LandEnrichmentService(
            client=FakeClient(payload),
            model_name="test-model",
        )
        result = await service.extract(text)
        self.assertEqual(
            [record.holder.value for record in result.records],
            ["APK Minerals Pvt.Ltd", "APK Minerals Pvt.Ltd"],
        )
        self.assertTrue(
            all(
                record.holder.evidence.method == "llm_with_ocr_evidence"
                for record in result.records
            )
        )
```

- [ ] **Step 4: Run the LLM enrichment tests and confirm failures**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_enrichment -v`

Expected: failures because `LandEnrichmentService` and validation are absent.

- [ ] **Step 5: Implement the injectable enrichment service**

```python
class LandEnrichmentService:
    def __init__(self, client=None, model_name: str | None = None):
        self.client = client
        self.model_name = model_name or settings.llm_model_name

    async def extract(self, text: str) -> LandExtractionResult:
        deterministic, candidates = build_deterministic_land_result(text)
        if self.client is None:
            deterministic.status = "not_configured"
            deterministic.warnings.append(
                "LLM is not configured; holder and contextual fields were not inferred."
            )
            return deterministic
        payload = await self._request_payload(text, candidates)
        return self._merge_validated(text, candidates, deterministic, payload)
```

The prompt must require JSON only, null for unsupported fields, exact
`evidence_text`, one record per parcel, and no inferred coordinates. Send only
raw OCR text and serialized deterministic candidates. Do not log either.

Use Unicode NFC plus collapsed whitespace for evidence containment. Preserve
the exact original OCR evidence in responses. Require survey, area, date, and
coordinate values to exist in deterministic candidate sets. Reuse
deterministic critical fields rather than replacing them with LLM values.

Catch provider, timeout, JSON, and schema failures inside `extract`; return the
deterministic result with `status="partial"` and a controlled warning.

- [ ] **Step 6: Run all enrichment tests**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_enrichment -v`

Expected: PASS for no-key fallback, supported evidence, repeated shared holder,
and hallucination rejection.

- [ ] **Step 7: Record the task checkpoint**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_candidates tests.test_land_enrichment -v`

Expected: PASS. No commit is possible unless Git is initialized.

### Task 5: Additive Land Endpoints and OCR Failure Isolation

**Files:**
- Create: `app/api/land_routes.py`
- Modify: `app/main.py`
- Create: `tests/test_land_routes.py`
- Modify: `tests/test_routes.py`

**Interfaces:**
- Consumes: existing `ocr_endpoint(file, prompt)` and `LandEnrichmentService.extract(text)`.
- Produces: `POST /land/extract` and `POST /ocr/land`.

- [ ] **Step 1: Write a failing standalone text-enrichment endpoint test**

```python
import io
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from PIL import Image

from app.api import land_routes
from app.main import app
from app.models.response_models import OCRResponse
from app.services.land_enrichment import LandEnrichmentService
from app.services.quality_assessment import assess_ocr_quality


def png_bytes():
    content = io.BytesIO()
    Image.new("RGB", (32, 24), "white").save(content, format="PNG")
    return content.getvalue()


def base_ocr_response():
    return OCRResponse(
        success=True,
        filename="sample.png",
        processing_time=0.1,
        text="base OCR text",
        confidence=91.5,
        quality=assess_ocr_quality("base OCR text", 91.5),
    )


class LandRouteTests(unittest.TestCase):
    def test_land_extract_accepts_existing_ocr_text_without_rerunning_ocr(self):
        service = LandEnrichmentService(client=None, model_name="unused")
        with patch.object(land_routes, "_land_service", service):
            response = TestClient(app).post(
                "/land/extract",
                data={"ocr_text": "207/9 (0.04.00)"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["records"][0]["survey_number"]["value"],
            "207/9",
        )
```

- [ ] **Step 2: Write a failing combined-endpoint failure-isolation test**

```python
class OCRLandFailureIsolationTests(unittest.TestCase):
    def test_ocr_land_keeps_successful_ocr_when_enrichment_fails(self):
        with patch(
            "app.api.land_routes.ocr_endpoint",
            new=AsyncMock(return_value=base_ocr_response()),
        ):
            with patch(
                "app.api.land_routes._land_service.extract",
                new=AsyncMock(side_effect=RuntimeError("provider down")),
            ):
                response = TestClient(app).post(
                    "/ocr/land",
                    files={"file": ("sample.png", png_bytes(), "image/png")},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ocr"]["text"], "base OCR text")
        self.assertEqual(response.json()["land_extraction"]["status"], "failed")
        self.assertNotIn("provider down", response.json()["land_extraction"]["error"])
```

- [ ] **Step 3: Strengthen the existing OCR-independence test**

Add a patch whose invocation would fail the test if `/ocr` touches enrichment:

```python
class StandaloneOCRIndependenceTests(unittest.TestCase):
    def test_ocr_does_not_invoke_land_enrichment(self):
        class FakeOCREngine:
            @staticmethod
            def extract_text(image):
                return "base OCR text", 91.5

        with patch(
            "app.api.routes._get_or_raise_ocr_engine",
            return_value=FakeOCREngine(),
        ):
            with patch(
                "app.api.land_routes._land_service.extract",
                side_effect=AssertionError("standalone OCR invoked enrichment"),
            ):
                response = TestClient(app).post(
                    "/ocr",
                    files={"file": ("sample.png", png_bytes(), "image/png")},
                )
        self.assertEqual(response.status_code, 200)
```

- [ ] **Step 4: Run endpoint tests and confirm 404 or import failures**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_routes tests.test_routes -v`

Expected: `/land/extract` and `/ocr/land` fail because the router is absent.

- [ ] **Step 5: Implement `land_routes.py` without modifying `/ocr` execution**

```python
router = APIRouter()


@router.post("/land/extract", response_model=LandExtractionResult)
async def extract_land_endpoint(ocr_text: str = Form(...)):
    if not ocr_text.strip():
        raise HTTPException(status_code=400, detail="OCR text must not be empty.")
    return await _land_service.extract(ocr_text)


@router.post("/ocr/land", response_model=OCRLandResponse)
async def ocr_land_endpoint(file: UploadFile = File(...)):
    ocr = await ocr_endpoint(file=file, prompt=None)
    try:
        land = await _land_service.extract(ocr.text)
    except Exception:
        logger.exception("Land enrichment failed after successful OCR.")
        land = LandExtractionResult(
            status="failed",
            records=[],
            record_count=0,
            requires_human_review=True,
            warnings=["Land enrichment failed; the base OCR result is still available."],
            error="Land enrichment failed.",
        )
    return OCRLandResponse(ocr=ocr, land_extraction=land)
```

Initialize the service with an `AsyncOpenAI` client only when
`settings.llm_api_key` is set. Include the new router in `app/main.py` after the
existing OCR router. Keep `app/api/routes.py` free of land-service imports.

- [ ] **Step 6: Run endpoint and independence tests**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_routes tests.test_routes -v`

Expected: PASS; `/ocr` succeeds while enrichment is patched to fail.

- [ ] **Step 7: Record the task checkpoint**

Run: `.\venv\Scripts\python.exe -m unittest tests.test_land_routes tests.test_routes -v`

Expected: PASS. No commit is possible unless Git is initialized.

### Task 6: Documentation and Full Regression Verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Documents: `/ocr` independence, `/land/extract`, `/ocr/land`, evidence semantics, explicit-coordinate-only behavior, and no-LLM fallback.

- [ ] **Step 1: Add API examples to the README**

Document this PowerShell call:

```powershell
curl.exe -X POST "http://localhost:8000/land/extract" `
  -F "ocr_text=207/9 (0.04.00), APK Minerals Pvt.Ltd"
```

Document the combined upload:

```powershell
curl.exe -X POST "http://localhost:8000/ocr/land" `
  -F "file=@C:\path\to\document.png"
```

State prominently that `/ocr` remains standalone, evidence is OCR evidence
rather than source verification, coordinates are never geocoded, and every
critical value requires human review.

- [ ] **Step 2: Run the complete unit suite**

Run: `.\venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: all existing OCR, PDF, quality, evaluation, and new land tests pass.

- [ ] **Step 3: Check installed dependency consistency**

Run: `.\venv\Scripts\python.exe -m pip check`

Expected: `No broken requirements found.`

- [ ] **Step 4: Verify the standalone OCR health path through Uvicorn**

Run the existing in-process Uvicorn health-check script and request
`http://127.0.0.1:8765/health`.

Expected: `200 {"status":"healthy"}`.

- [ ] **Step 5: Verify deterministic land extraction without an LLM key**

Run a TestClient request to `/land/extract` with:

```text
APK Minerals Pvt.Ltd 207/9 (0.04.00), 208/2B1 (0.08.50)
```

Expected: HTTP 200, two separate records, deterministic survey/area evidence,
`source_verified=false`, and `status="not_configured"` when no key is present.

- [ ] **Step 6: Record the final checkpoint**

Run: `.\venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: all tests pass. No commit is possible unless Git is initialized.
