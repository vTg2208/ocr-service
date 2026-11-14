# Open-source satellite integration

Implemented on `feature/open-source-model-integrations`, using the final-phase
integration requirements in the supplied project presentation.

## What runs

An authenticated user or member of staff requests analysis of a mapped FRA
case or an approved personal-plot snapshot. A durable `land_cover` job:

1. Captures the exact boundary version and requested date range.
2. Finds a Sentinel-2 L1C acquisition through Earth Search STAC.
3. Matches its L2A acquisition and MGRS tile for the SCL quality mask.
4. Reads B02, B03, B04, B05, B06, B07, B08, B11 and B12 on the blue band's
   10 m grid with bilinear resampling and 32 pixels of context.
5. Applies each provider band's radiometric scale/offset, then the published
   log-percentile normalization. It excludes cloud, shadow, defective and
   missing pixel centres from target statistics.
6. Runs real pretrained Dynamic World inference on CPU in bounded tiles.
7. Privately retains prepared bands, the validity mask, label GeoTIFF and PNG
   preview, with checksums. It stores clipped GeoJSON and per-class summaries.
8. Presents results, provenance, download links and a printable report to the
   owner and authorized staff. Source URLs and storage keys are not returned.
9. Requires a staff review before projecting results into owner resource
   observations or the existing FRA asset/Atlas/profile/DSS pipeline.

Repeated identical requests reuse their existing analysis/job. Editing a draft
does not mutate its approved snapshot. Superseding an approved snapshot, or
changing an FRA geometry version, prevents stale results from being published.
Version-bound FRA observations are excluded from current DSS facts after a
boundary change. Older or repeated acquisitions cannot replace/duplicate newer
FRA asset evidence.

The personal plot timeline now reflects queued, running, completed, reviewed,
failed and insufficient-data states. The shared dialog is available from My
plots, staff submission review, FRA case details and Asset Intelligence.

The existing FRA Atlas, Asset Intelligence, DSS Planner and Reports controls
are now accessible; their former development overlays have been removed.

## Model provenance and limitations

- Original model: [Google Dynamic World](https://github.com/google/dynamicworld),
  [Brown et al., 2022](https://doi.org/10.1038/s41597-022-01307-4).
- Runtime: [Caleb Robinson's PyTorch port](https://github.com/calebrob6/dynamic_world_pytorch),
  pinned at `626dc9114145f31534c5d6cebd28816f81c5e284`.
- Code and pretrained weights: Apache-2.0. Attribution and the license are in
  `app/services/vendor/NOTICE.dynamic-world` and `LICENSE.dynamic-world`.
- Weights SHA-256:
  `a70b3a60ceec1bd468dee75b94d30c511ecfbf19b31cd4a3c205c3dc2b8efb94`.
- `torch.load` uses `weights_only=True`; unknown checksums are refused.
- Nine output classes: water, trees, grass, flooded vegetation, crops,
  shrub/scrub, built area, bare ground, and snow/ice.

This is an open-source baseline substituted at the user's request. It does not
implement the presentation's trained B04+B08+B12 four-class U-Net. Its nine-band
input contract and scores must not be confused with that research experiment.
No local Tamil Nadu accuracy, F1, or IoU is claimed.

Trees are not automatically classified as legal forest. Built area does not
establish individual homesteads, roads, wells or public infrastructure. Only
reviewed water/crop predictions map directly to `water_body` and
`agricultural_land`; other classes retain their source label under `other_asset`.
Cloud-obscured pixels are unknown, not absent assets. Cover fractions projected
into FRA facts use all target pixel centres as the denominator, rather than
extrapolating clear pixels across the obscured area.

Areas are pixel estimates at 10 m; B05/B06/B07/B11/B12 originate at 20 m and are
resampled. Boundaries with fewer than nine usable pixel centres receive
`insufficient_resolution`, with no fabricated detections. Fragmented outputs
have a bounded vector-complexity limit and retain their raster/summary.
An acquisition without a matching SCL mask receives `insufficient_imagery`.
Scene discovery is deliberately bounded; it does not exhaustively mosaic scenes.

## Install and run with Docker

The API keeps its existing runtime. The worker image additionally installs the
CPU PyTorch dependency from `requirements-ml.txt`. Model weights use a named
volume and are installed explicitly:

```powershell
docker compose build api worker
docker compose run --rm --no-deps worker python -m scripts.install_land_cover_model
docker compose up -d api worker
docker compose exec api python -m scripts.migrate
docker compose exec api alembic current
```

Expected migration head: `20261114_0017`. Do not reset database volumes.

For an existing local Python 3.11 environment:

```powershell
python -m pip install -r requirements-dev.txt
python -m pip install -r requirements-ml.txt --index-url https://download.pytorch.org/whl/cpu
python -m scripts.install_land_cover_model
python -m scripts.migrate
python -m scripts.run_fra_jobs --forever
```

Weights are never downloaded silently at request time. Missing weights/runtime
produce a visible job failure; staff can install them and retry from the dialog.
Raw L1C JP2 access can be substantially slower than L2A COG access. The existing
worker renews leases during long acquisition/inference runs.

## API

| Method | Route | Purpose |
| --- | --- | --- |
| GET/POST | `/api/fra/claims/{id}/land-cover` | List/request FRA boundary analysis |
| GET/POST | `/api/user/plots/{id}/land-cover` | List/request analysis of the owner's approved snapshot |
| GET/POST | `/api/staff/portal/submissions/{id}/land-cover` | Staff analysis of an approved snapshot |
| GET | `/api/land-cover/{id}` | Private status and results |
| POST | `/api/land-cover/{id}/review` | Reviewer/admin verification or rejection, note and revision |
| GET | `/api/land-cover/{id}/geojson` | Clipped class polygons |
| GET | `/api/land-cover/{id}/artifacts/{kind}` | Private `input`, `validity`, `labels` or `preview` |
| GET | `/api/land-cover/{id}/report` | Protected printable evidence report |

POST requests accept `start_date` and `end_date` in ISO date format. Ranges are
limited to 93 days and cannot extend into the future. Default: preceding
90 days through yesterday. Reviews require `state`, `note` and
`expected_revision`. Review does not grant a legal right or scheme benefit.
All downloads/reports require current authorization and produce audit events.

## Validation

Regression tests cover API authorization, idempotency, immutable snapshots,
stale review rejection, worker failures, input/mask retention, real raster
alignment/offsets, cloud masking, review-only publication, FRA facts, model
checksum/inference, DOM requests, polling and review controls.

On 14 November 2026, a live smoke check used a small public validation window
around the centroid of the bundled Arpisampalaiyam village polygon. It found
`S2C_44PLU_20260305_0_L1C` and its matching L2A acquisition, read a real
9 × 109 × 109 raster, and classified 1,936 valid target pixel centres. It wrote
a georeferenced label raster and preview. This validates execution on real
imagery; it is not a ground-truth accuracy evaluation or an authoritative parcel
assessment. Low model probabilities generated an explicit warning.

Repeat the real public-window check (network/raster reads may take several minutes):

```powershell
python -m scripts.validate_land_cover --public-validation-window --start 2026-01-01 --end 2026-03-31 --output-directory tmp/land-cover-validation
```

The output directory must be empty. It retains prepared input, validity mask,
predicted raster, preview, clipped GeoJSON and a checksum/provenance manifest.
Use `--geometry path/to/boundary.geojson` instead for an explicitly selected
boundary. This CLI does not create claims, plot approvals or legal records.

Docker/PostGIS execution and visual browser validation must still be repeated
in the deployment environment. The automated suite uses isolated SQLite
databases for most workflows; service-dependent tests remain conditional.

## Remaining presentation work

| Presentation item | State after this implementation |
| --- | --- |
| Research/application inference connection | Operational open-source baseline, kept distinct from compact-band research |
| Personal-plot satellite runner | Implemented over approved snapshots |
| Atlas/assets/DSS/reports interfaces | Existing interfaces exposed; reviewed inference feeds FRA assets and DSS |
| Verified Tamil OCR CER/WER benchmark | Evaluation tools exist; verified labelled data still required |
| All-12-band and wider U-Net experiments | Research data/training work remains; no results invented |
| Document-image evidence overlays | Still to implement |
| Authoritative cadastral coverage | Import machinery exists; licensed authoritative source still required |
| Production OIDC and shared rate limiter | Still to implement/configure |
| Production contact delivery | Development terminal OTP remains; real provider still required |
| Final evaluation, deployment, thesis/paper | Requires ground truth, deployment environment, and research artifacts |
