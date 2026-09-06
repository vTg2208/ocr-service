# AranyaSetu demonstration flow

The demonstration follows the FRA lifecycle in the same order as the product.
Run the pilot first so every implemented stage has a stable record to open:

```powershell
docker compose up -d --build api worker
docker compose exec -T api python -m scripts.seed_tamil_nadu_fra_pilot
```

The command returns the ordered `stages` array below plus the village, case,
scene, recommendation, and report identifiers. Open
`http://localhost:8000/login`, sign in with the configured demo code, and use
the returned identifiers during the walkthrough.

| Step | Demonstrate | Workspace/action | Pilot result |
| ---: | --- | --- | --- |
| 1 | Upload/import legacy FRA documents | **Case Management → Legacy records**; show scan/PDF batch and CSV/XLSX intake | `complete_with_synthetic_fixture`; no public claim-level FRA document is bundled |
| 2 | OCR documents | Open the pilot archive record and its extraction metadata | `complete_with_synthetic_fixture`; validates orchestration, not real-form accuracy |
| 3 | Extract FRA entities | Show holder/community, village, right type, status, references, area, and field evidence | `complete_with_synthetic_fixture` with explicit adapter version |
| 4 | Review extracted information | Show per-field source, confidence, correction, final value, reviewer, and time | `complete` |
| 5 | Create/normalize FRA records | Open the promoted native CFR case | `complete_with_synthetic_fixture`; claimant/case is visibly synthetic |
| 6 | Link records to villages | Show village `632998` and geometry version 1 | Real village boundary with a clearly synthetic inset claim geometry |
| 7 | Display records on the FRA Atlas | Filter Villupuram → Kandamangalam → Arpisampalaiyam | `complete`; village and claim features are returned |
| 8 | Display claim/title status | Open the case from the Atlas and show its lifecycle/title panel | `complete`; pilot case is submitted and has no active title |
| 9 | Load satellite imagery | Open **Asset Intelligence** and show scene `S2A_44PLU_20260605_0_L2A` | `complete`; real sanitized Sentinel-2 discovery metadata |
| 10 | Run AI asset detection | Show the model attachment panel | `awaiting_user_model`; attach the user's trained detector before inference |
| 11 | Display agricultural/water/homestead assets | Show the reviewed asset register and Atlas asset layer | `awaiting_user_model`; empty state is intentional and no detections are fabricated |
| 12 | Generate village asset profile | Show the Arpisampalaiyam profile | `complete_with_observation_gap`; zero reviewed assets and missing-observation indicators |
| 13 | Generate DSS facts | Select the pilot case in **DSS Planner** and derive facts | `complete`; snapshot uses `fra-dss-facts-v1` |
| 14 | Run scheme rules | Show the governed `TN-PILOT-JJM-READINESS` rule and catalogue version | `complete`; pilot-only rule execution |
| 15 | Show recommended schemes/interventions | Open the resulting convergence card | `insufficient_data`; it requests reviewed water/asset evidence |
| 16 | Show spatial/evidence reasoning | Expand evidence, reasons, missing inputs, rule version, and source facts | `complete`; the result remains advisory |
| 17 | Generate a report | Open the returned `report_url` in **Reports** | `complete`; the runner also returns the rendered report SHA-256 |

## Asset-model handoff

Steps 10 and 11 are truthful placeholders until the trained model is attached.
The rest of the demonstration continues because an empty, versioned village
profile and an `insufficient_data` DSS result are valid operational states.
After attaching the model:

1. register and activate its exact version, checksum, label map, preprocessing
   contract, evaluation metrics, and adapter configuration;
2. run inference on the analysis-ready claim raster;
3. review the resulting `fra-assets-v1` observations;
4. rebuild the `village-assets-v2` profile;
5. derive a new `fra-dss-facts-v1` snapshot and rerun governed rules.

This sequence changes steps 10–12 only when real reviewed detections exist. It
does not convert model output into a legal finding or an official scheme
eligibility decision.

## Demonstration evidence

The pilot runner is idempotent. Its second run reports `created: 0` while still
rendering the same ordered contract. The stage report identifies which inputs
are published public references, synthetic workflow fixtures, real sanitized
imagery metadata, or deferred model output. The source classification must stay
visible during the demonstration.
