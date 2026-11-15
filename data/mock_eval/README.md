# MOCK evaluation samples — not a benchmark

Everything in this folder is **invented** (`"mock": true`) to exercise
`scripts/evaluate_fra_models.py` and the tests. The numbers it produces say
nothing about real OCR, extraction or detection quality.

Never mix these files with `data/real/`, and never cite results from them as
evaluation. Real benchmarks need human-verified labels from real documents.

The evaluator marks mock outputs `data_kind: mock`, `benchmark_eligible: false`,
excludes the `mock` metadata from entity metrics, and rejects mixed real/mock
datasets. Tamil/English OCR fixtures are supplied solely to exercise CER/WER
and critical-field metrics; their predictions are invented, not model output.

```powershell
python -m scripts.evaluate_fra_models --task ocr --labels data/mock_eval/ocr_labels.json --predictions data/mock_eval/ocr_predictions.json
```

Smoke run:

    python -m scripts.evaluate_fra_models --task entity_extraction \
      --labels data/mock_eval/entity_labels.json --predictions data/mock_eval/entity_predictions.json
