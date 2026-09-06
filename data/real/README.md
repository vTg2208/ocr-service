# Tamil Nadu real-data validation bundle

This directory contains a small public, non-synthetic validation bundle. It is source data for validation, not a claim-level pilot and not proof of any person's FRA rights.

- `arpisampalaiyam_village.geojson` contains the Survey of India village polygon for Census village `632998`, downloaded through the National Water Data Portal and transformed from EPSG:7755 to EPSG:4326. The feature retains source, version, CRS, authority, download, and licence references.
- `tamil_nadu_fra_progress_2026-06-30.json` transcribes the Tamil Nadu state row from page 7 of the Ministry of Tribal Affairs June 2026 FRA monthly progress report. Its component counts, total counts, source page, URL, and source-document SHA-256 are retained. These aggregates must never be expanded into invented claim or title records.
- `arpisampalaiyam_sentinel2_scene.json` records the sanitized result of a live Earth Search query for a Sentinel-2 L2A scene intersecting that village. Provider asset URLs are deliberately excluded.
- `validation_report.json` records structural validation and the privacy-safe result of running the supplied real revenue Patta scan through the actual OCR stack. The scan is correctly classified as supporting cadastral evidence, not as an FRA claim/title document; neither OCR text nor extracted personal values are stored here.

Run the offline public-bundle checks with:

```powershell
.\venv\Scripts\python.exe -m scripts.validate_real_tamil_nadu_data
```

To repeat the local evidence-scan validation without copying the source scan into the repository:

```powershell
.\venv\Scripts\python.exe -m scripts.validate_real_tamil_nadu_data --cadastral-image C:\path\to\scan.png --output data\real\validation_report.json
```

Claim-level FRA documents, individual title records, and their legal geometries are intentionally absent because no verified, publishable Tamil Nadu sample was provided. The bundle reports these limitations rather than substituting synthetic records.
