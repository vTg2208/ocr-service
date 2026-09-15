# FRA digitization completion

User-approved scope: complete the existing Tamil Nadu scanned-document digitization workflow with actual multilingual NER, traceable extraction, coordinates/status normalization, human review, continuous resilient processing, and measured verification. Work inline in the current checkout, preserving previous staged changes and user data.

## Acceptance criteria

- Real images and PDFs produce bounded, page-aware OCR blocks with coordinates, recognition scores and stable text offsets. Existing plain-text OCR callers remain compatible.
- A pinned multilingual GLiNER model can execute locally through an allow-listed adapter. Missing weights/dependencies fail explicitly; fixture extraction is never presented as actual NER.
- Label, NER and coordinate candidates retain exact source spans. Conflicting candidates remain unresolved; officer/witness names are not silently selected as holders. Normalized status is separate from its source expression and never issues a legal decision.
- Extraction runs retain OCR pages, per-field evidence, candidates, validation warnings, model versions and raw source values. Reviewer corrections preserve historical output. Coordinate review supports explicit missing/ambiguous values without geocoding or inventing boundaries.
- An authorized reviewer can inspect the original page beside fields and source highlights, correct values and promote a reviewed record. Source access follows the existing archive privacy policy. Processing status refreshes automatically.
- Docker supplies a continuous worker. Leases, attempt fencing, recovery and retry delays prevent abandoned jobs and duplicate committed domain writes.
- Tests cover real model inference and rendered synthetic scans where runtime support permits, plus SQLite/PostGIS and browser contracts. Accuracy on real documents is only claimed with representative independently labeled records.

## Implementation sequence

- [ ] 1. Preserve OCR blocks/pages and bound image/PDF work; verify legacy OCR compatibility.
- [ ] 2. Implement evidence validation, hybrid entity extraction, status/coordinate normalization and the real GLiNER adapter; install/pin and smoke-test the actual model.
- [ ] 3. Integrate extraction persistence, archive filters/review and protected source previews; test API authorization and immutable history.
- [ ] 4. Complete side-by-side source review, field highlights, coordinates and job refresh; test actual browser behavior.
- [ ] 5. Add continuous worker, leases, fencing, crash recovery, backoff and explicit migration; test overlapping workers and populated-schema upgrade.
- [ ] 6. Add repeatable scan evaluation, run real OCR/NER on labeled synthetic fixtures, run full Python/JavaScript/PostGIS checks, and document measured outcomes and real-data limits.

## Data and model constraints

Use existing code/data fixtures and generated synthetic documents until the user provides an evaluation folder. Never read or change the application database, uploaded originals, .env secrets, pre-existing untracked report/icon/problem statement, or external private data. Model weights are downloaded locally from their public repository; documents are not sent to external inference services. Human review remains required before archive promotion.
