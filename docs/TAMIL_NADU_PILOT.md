# Tamil Nadu FRA pilot

The limited pilot covers **Arpisampalaiyam village, Kandamangalam block,
Villupuram district**. It is a runnable proof of the FRA data flow, with source
classification retained at every stage.

| Stage | Pilot input | Classification |
| --- | --- | --- |
| Selected geography | Survey of India Census village `632998` | Published, authoritative public reference |
| FRA document | One clearly named pilot fixture | Synthetic; not a legal record |
| OCR/NER | Deterministic fixture transcription and entity adapter | Synthetic workflow validation; not a real-document accuracy result |
| Structured FRA record | Reviewed and promoted native CFR case | Synthetic; not a real claimant |
| Spatial linking | Inset polygon linked to village `632998` | Synthetic; not a legal boundary |
| FRA Atlas | Real village plus synthetic spatial FRA case | Mixed provenance shown on each record |
| Satellite imagery | Sentinel-2 L2A scene `S2A_44PLU_20260605_0_L2A` | Real, sanitized STAC discovery metadata |
| Satellite asset mapping | Production model attachment boundary | Awaiting the user-supplied trained model; no detections are generated |
| Village asset profile | Persisted `village-assets-v2` profile | Complete with explicit observation gaps |
| DSS | `fra-dss-facts-v1` plus a governed pilot readiness rule | Advisory pilot evaluation |
| Scheme recommendation | JJM evidence-readiness result | `insufficient_data` until reviewed model observations exist |

Run the idempotent pilot after migrations:

```powershell
docker compose exec api python -m scripts.seed_tamil_nadu_fra_pilot
```

The command returns the exact 17-step ordered demonstration contract, including
stable village, case, scene, recommendation, and report references. A successful
current run reports asset detection and mapped-asset display as
`awaiting_user_model`, the village profile as `complete_with_observation_gap`,
and the scheme recommendation as `insufficient_data`. Those states prevent the
pilot from inventing model detections or scheme eligibility. Follow
[`DEMO_FLOW.md`](DEMO_FLOW.md) for the presenter sequence.

The pilot uses a separate `TN-PILOT-JJM-READINESS` catalogue code. Its only
purpose is to exercise governed evidence-readiness evaluation without replacing
or activating an operational JJM scheme configuration. The DSS output always
requires departmental review and cannot approve or sanction a benefit.

The real public source bundle and its limitations are documented in
[`data/real/README.md`](../data/real/README.md). Attach the trained detector using
[`MODEL_ADAPTERS.md`](MODEL_ADAPTERS.md); after inference and human review,
refresh the village asset profile and derive a new fact snapshot before running
an operational scheme rule.
