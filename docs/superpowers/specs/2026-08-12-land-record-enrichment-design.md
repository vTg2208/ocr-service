# Land Record Enrichment Design

## Objective

Add optional structured land-record inference on top of the existing OCR
service. The current PaddleOCR text extraction remains independently usable
and must not depend on an LLM or the enrichment pipeline.

## Goals

- Preserve `POST /ocr` as the standalone base OCR operation.
- Extract one structured record per parcel or survey-number/area association.
- Return holder names, explicit coordinates, parcel area, survey numbers,
  administrative location, land type/use, document references, and other
  supported land information.
- Require OCR evidence for every inferred field.
- Keep critical numeric fields unverified until reviewed against the source.
- Return deterministic partial results when the LLM is unavailable.

## Non-goals

- Do not alter or automatically correct raw OCR text.
- Do not estimate parcel coordinates by geocoding a village or address.
- Do not claim that OCR confidence is textual or factual accuracy.
- Do not mark any extracted legal or land value as source-verified.
- Do not let enrichment failures cause standalone OCR failures.

## Architecture

The feature has three isolated layers:

1. Base OCR extracts raw text and quality warnings using the existing model.
2. A deterministic candidate extractor finds exact textual candidates.
3. An optional LLM groups and labels candidates, followed by strict validation.

```text
Document -> Base OCR -> raw OCR response
                       |
                       +-> deterministic land candidates
                           -> optional constrained LLM grouping
                           -> evidence validator
                           -> separate land records
```

The base OCR layer does not import, initialize, or invoke the land-enrichment
service.

## API Design

### `POST /ocr`

Remains the independent OCR endpoint. Its current response and behavior remain
compatible. It does not perform land enrichment and does not require an LLM
API key.

### `POST /land/extract`

Accepts `ocr_text` as form data and returns structured land records. This lets
clients store OCR output and enrich it later without rerunning OCR.

### `POST /ocr/land`

Accepts the same file upload as `/ocr`. It runs base OCR first and then invokes
land enrichment. Its response contains the complete base OCR result plus a
separate `land_extraction` object.

If enrichment fails, this endpoint returns the successful OCR result with
`land_extraction.status = "failed"` and an error message. An OCR failure still
uses the existing OCR error response and status code.

## Output Schema

`land_extraction` contains:

- `status`: `completed`, `partial`, `not_configured`, or `failed`.
- `records`: separate parcel records.
- `record_count`: number of records.
- `requires_human_review`: true when records contain unverified fields.
- `warnings`: document-level warnings.
- `error`: controlled enrichment failure details, otherwise null.

Each parcel record contains:

- `record_id`: stable response-local identifier such as `land-1`.
- `holder`: person or organization name, holder type, and evidence.
- `survey_number`: parcel/survey identifier and evidence.
- `area`: raw value, explicit unit when available, optional normalized square
  metres, and evidence.
- `latitude` and `longitude`: only explicit coordinates from OCR text.
- `location`: village, taluk, district, state, address, and evidence per field.
- `land_type`: for example patta land, only when supported by OCR evidence.
- `uses_or_resources`: supported land use, minerals, crops, or similar details.
- `document_references`: relevant dates, reference numbers, and authority.
- `other_attributes`: additional evidence-backed key/value information.
- `warnings`: parcel-level ambiguity and verification warnings.

Missing scalar values are null and missing collections are empty arrays.

## Evidence Model

Every populated field includes:

- `text`: an exact substring from OCR output.
- `method`: `deterministic`, `llm_with_ocr_evidence`, or `hybrid`.
- `confidence`: extractor confidence from 0 to 1, not source accuracy.
- `source_verified`: always false in automated responses.

The validator rejects an LLM field when its evidence text is not present in the
OCR text after Unicode and whitespace normalization. Numeric values, survey
numbers, dates, and coordinates must also match a deterministic candidate.

## Deterministic Candidate Extraction

The deterministic layer extracts:

- Survey identifiers such as `207/9` and `208/2B1`.
- Area strings and nearby units.
- Decimal coordinates and degree-minute-second coordinate pairs.
- Dates and document/reference numbers.
- Recognizable administrative labels and nearby text.
- Existing mixed-script and quality warnings.

Latitude must be in `[-90, 90]` and longitude in `[-180, 180]`. A coordinate is
not returned unless both values are explicit and can be paired from nearby OCR
text.

Area normalization occurs only when the unit or area notation is explicit
enough to support a deterministic conversion. Otherwise, the raw value is
returned and normalized square metres remains null.

## LLM Enrichment

The configured OpenAI-compatible LLM receives:

- Raw OCR text.
- Deterministic candidates with source spans.
- A strict JSON schema.
- Instructions to group parcels, identify holders, associate shared location
  context, and return null rather than infer unsupported values.

Shared holder or location details may be repeated across parcel records when
the document clearly applies them to all listed parcels. The same evidence is
included in each repeated field.

The LLM cannot introduce numeric, coordinate, date, or survey values that are
absent from deterministic candidates. Validation runs after deserialization.

## Behavior Without an LLM

`/ocr` is unaffected.

The land endpoints return deterministic parcel records when survey/area
candidates exist. Semantic fields such as holder and land use remain null or
empty. Status is `partial` or `not_configured`, and warnings state which fields
could not be inferred.

## Error Handling

- Invalid or empty `ocr_text`: HTTP 400 from `/land/extract`.
- OCR failure in `/ocr/land`: existing OCR error response.
- LLM timeout, malformed JSON, or unavailable provider: retain deterministic
  records and return `partial` when possible; otherwise return `failed`.
- Unsupported or non-evidenced LLM values: discard the field and add a warning.
- Ambiguous parcel association: retain candidates in separate records where
  possible and require human review rather than guessing.

## Testing

Tests cover:

- `/ocr` works with no LLM key and never invokes land extraction.
- Deterministic survey, area, date, and coordinate candidate extraction.
- Explicit-coordinate-only behavior and coordinate range validation.
- Separate records for multiple survey parcels.
- Evidence substring enforcement.
- Rejection of hallucinated numeric and coordinate values.
- Holder and location grouping from a constrained fake LLM response.
- Partial output when no LLM key is configured.
- Failure isolation in `/ocr/land`.
- Existing OCR, PDF, quality, and evaluation tests remain passing.

## Security and Privacy

OCR text may contain personal and land-ownership information. The service does
not log raw OCR text or inferred holder details. LLM enrichment sends OCR text
to the configured provider only when a land-enrichment endpoint is explicitly
called.
