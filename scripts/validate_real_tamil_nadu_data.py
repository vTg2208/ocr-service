"""Validate the source-traceable Tamil Nadu FRA public-data bundle and optional evidence scan."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from app.services.fra_archive import REQUIRED_REVIEW_FIELDS
from app.services.fra_entity_extraction import TamilNaduFRAExtractor
from app.services.fra_job_handlers import _recognize_archive_document
from app.services.real_data_validation import load_and_validate_public_bundle


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data" / "real"


def validate_cadastral_evidence(path: Path) -> dict:
    """Run the real OCR stack while returning no OCR text or personal field values."""

    content = path.read_bytes()
    raw_text, confidence, model_version, _elapsed_ms, pages = _recognize_archive_document(
        content, path.name
    )
    extraction = TamilNaduFRAExtractor("tn-fra-regex-v1").extract(
        path.name,
        {"raw_text": raw_text, "pages": pages, "state_code": "TN"},
    )
    extracted = set(extraction.fields)
    missing_required = sorted(REQUIRED_REVIEW_FIELDS - extracted)
    return {
        "document_role": "cadastral_evidence_only",
        "fra_document": False,
        "source_file_sha256": hashlib.sha256(content).hexdigest(),
        "source_file_size": len(content),
        "ocr_model_version": model_version,
        "ocr_confidence": confidence,
        "ocr_page_count": len(pages),
        "ocr_line_count": sum(len(page.get("lines") or []) for page in pages),
        "entity_model_version": extraction.model_version,
        "entity_confidence": extraction.confidence,
        "extracted_field_names": sorted(extracted),
        "missing_required_fra_fields": missing_required,
        "fra_promotion_ready": not missing_required,
        "review_disposition": "retain_as_supporting_cadastral_evidence",
        "privacy": "Raw OCR text and extracted personal values are intentionally omitted.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument(
        "--cadastral-image", type=Path,
        help="Optional real Patta/revenue scan used only for OCR and supporting-evidence validation.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = load_and_validate_public_bundle(args.data_dir)
    if args.cadastral_image:
        report["cadastral_evidence_validation"] = validate_cadastral_evidence(
            args.cadastral_image
        )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
