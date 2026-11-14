# MOCK evaluation samples — not a benchmark

Everything in this folder is **invented** (`"mock": true`) to exercise
`scripts/evaluate_fra_models.py` and the tests. The numbers it produces say
nothing about real OCR, extraction or detection quality.

Never mix these files with `data/real/`, and never cite results from them as
evaluation. Real benchmarks need human-verified labels from real documents.

Smoke run:

    python -m scripts.evaluate_fra_models --task entity_extraction \
      --labels data/mock_eval/entity_labels.json --predictions data/mock_eval/entity_predictions.json
