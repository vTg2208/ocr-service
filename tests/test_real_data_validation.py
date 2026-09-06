import copy
import json
from pathlib import Path

import pytest

from app.services.real_data_validation import (
    RealDataValidationError,
    load_and_validate_public_bundle,
    validate_public_bundle,
)
from scripts import validate_real_tamil_nadu_data as validation_script


REAL_DATA = Path(__file__).parents[1] / "data" / "real"


def test_real_tamil_nadu_bundle_has_traceable_fra_village_geometry_and_imagery_data():
    report = load_and_validate_public_bundle(REAL_DATA)

    assert report["status"] == "validated_with_limitations"
    assert report["synthetic_records"] == 0
    assert report["fra_progress"]["claims_received_total"] == 34667
    assert report["fra_progress"]["titles_distributed_total"] == 16508
    assert report["village"]["village_code"] == "632998"
    assert report["village"]["geometry_valid"] is True
    assert report["satellite_imagery"]["intersects_village"] is True
    assert report["satellite_imagery"]["private_asset_urls_persisted"] is False
    assert "claim_level_fra_records" in report["limitations"]


def test_real_bundle_rejects_inconsistent_official_progress_totals():
    village = json.loads((REAL_DATA / "arpisampalaiyam_village.geojson").read_text(encoding="utf-8"))
    progress = json.loads((REAL_DATA / "tamil_nadu_fra_progress_2026-06-30.json").read_text(encoding="utf-8"))
    imagery = json.loads((REAL_DATA / "arpisampalaiyam_sentinel2_scene.json").read_text(encoding="utf-8"))
    invalid = copy.deepcopy(progress)
    invalid["claims_received"]["total"] += 1

    with pytest.raises(RealDataValidationError, match="claims received total"):
        validate_public_bundle(village=village, progress=invalid, imagery=imagery)


def test_real_bundle_rejects_synthetic_or_nonintersecting_inputs():
    village = json.loads((REAL_DATA / "arpisampalaiyam_village.geojson").read_text(encoding="utf-8"))
    progress = json.loads((REAL_DATA / "tamil_nadu_fra_progress_2026-06-30.json").read_text(encoding="utf-8"))
    imagery = json.loads((REAL_DATA / "arpisampalaiyam_sentinel2_scene.json").read_text(encoding="utf-8"))
    synthetic = copy.deepcopy(village)
    synthetic["metadata"]["synthetic"] = True
    with pytest.raises(RealDataValidationError, match="synthetic"):
        validate_public_bundle(village=synthetic, progress=progress, imagery=imagery)

    outside = copy.deepcopy(imagery)
    outside["selected_scene"]["footprint"] = {
        "type": "MultiPolygon",
        "coordinates": [[[[76.0, 8.0], [76.1, 8.0], [76.1, 8.1], [76.0, 8.1], [76.0, 8.0]]]],
    }
    with pytest.raises(RealDataValidationError, match="does not intersect"):
        validate_public_bundle(village=village, progress=progress, imagery=outside)


def test_cadastral_scan_validation_is_privacy_safe_and_cannot_become_an_fra_record(tmp_path, monkeypatch):
    scan = tmp_path / "reference.png"
    scan.write_bytes(b"real scan bytes")
    private_text = (
        "Claimant: Private Person\nDistrict: Villupuram\nBlock: Kandamangalam"
    )
    monkeypatch.setattr(
        validation_script,
        "_recognize_archive_document",
        lambda _content, _filename: (
            private_text, 0.88, "ocr-test-v1", 20,
            [{"page_number": 1, "text": private_text, "confidence": 0.88, "lines": []}],
        ),
    )

    report = validation_script.validate_cadastral_evidence(scan)

    assert report["document_role"] == "cadastral_evidence_only"
    assert report["fra_document"] is False
    assert report["fra_promotion_ready"] is False
    assert "right_type" in report["missing_required_fra_fields"]
    assert "Private Person" not in json.dumps(report)


def test_recorded_real_scan_report_contains_no_path_or_extracted_values():
    report = json.loads((REAL_DATA / "validation_report.json").read_text(encoding="utf-8"))
    evidence = report["cadastral_evidence_validation"]
    rendered = json.dumps(evidence, ensure_ascii=False)

    assert evidence["source_file_sha256"] == "8cbcb6ac1236174dcb7b2965c1ddf61748aa6b714ee2c72c0d500706f6afbbe6"
    assert evidence["fra_document"] is False
    assert evidence["fra_promotion_ready"] is False
    assert "C:\\" not in rendered
    assert "raw_text" not in rendered
    assert "field_values" not in rendered
