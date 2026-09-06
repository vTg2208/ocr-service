# AranyaSetu operations

## Monitoring and alerts

Probe `/health` for process liveness and `/health/ready` for database readiness. Alert when readiness fails twice, 5xx responses exceed 2% for five minutes, document-processing latency exceeds the service objective, storage or ClamAV is unavailable, reviewer-queue age exceeds the target, disk/database capacity exceeds 80%, or a scheduled backup/restore verification fails.

Logs are structured as key/value access events with method, path, status, duration, and request ID. Forward them over encrypted transport and redact authorization headers and document/OCR content.

For the FRA domain, also alert on repeated satellite-provider `503` responses, abnormal DSS `insufficient_data` rates, overdue reviewer transitions, failed audit writes, and PostGIS spatial-query errors. Satellite and DSS alerts indicate operational state only; they must not be interpreted as legal or eligibility decisions.

## Backup and restore

Run `python scripts/postgres_backup.py backups/aranyasetu.dump` from an environment with `DATABASE_URL` and `pg_dump`. Encrypt backup storage and apply the approved retention policy.

At least monthly, restore into an isolated non-production database:

```text
python scripts/postgres_restore.py backups/aranyasetu.dump --confirm-destructive-restore
alembic upgrade head
python -m pytest tests/test_migrations.py tests/test_cadastral_evidence_api.py
```

Record the restore date, duration, row counts, PostGIS geometry validity check, and reviewer. Never test restoration against production.

After restoring an FRA-enabled database, confirm that `alembic heads` reports `20260906_0013`, then reconcile counts for FRA claims, decisions, geometry/title versions, intake/archive records, spatial imports, imagery scenes/artifacts, fact snapshots, scheme catalogue versions, processing jobs, model versions, assets, village profiles, referrals, reports, and audit events. Never repair history by deleting an earlier decision or version.

## Tamil Nadu FRA sample data and workers

Apply migrations before using the completed FRA workspaces:

```text
python -m alembic upgrade head
python -m scripts.seed_tamil_nadu_fra_pilot
python -m scripts.seed_tamil_nadu_fra_pilot
python -m scripts.run_fra_jobs --max-jobs 20
```

The pilot is idempotent: the first run reports created records and the second reports `created: 0`. It combines a real public village boundary and sanitized Sentinel-2 metadata with visibly synthetic FRA case/document/geometry fixtures. Do not run it in an authoritative database. The asset stage remains `awaiting_user_model`; no detections are fabricated. The protected `/fra` UI exposes FRA Overview, FRA Atlas, Case Management, Asset Intelligence, DSS Planner, and Reports with one shared Tamil Nadu context bar.

The worker uses durable `processing_jobs` rows and bounded retries. Alert on quarantined jobs, repeated failures, or a growing oldest-queued age. A missing or inactive model is an unavailable-model condition, not a successful inference. See `docs/MODEL_ADAPTERS.md` before attaching trained weights.

Historical discovery is bounded by `STAC_ENDPOINT`, `STAC_ALLOWED_HOSTS`, `STAC_ALLOWED_COLLECTIONS`, `STAC_TIMEOUT_SECONDS`, `STAC_MAX_PAGES`, `STAC_MAX_RESULTS`, and `STAC_MAX_CLOUD`. Keep the endpoint and every pagination host allow-listed. Provider failures are retryable and roll back scene/artifact rows and newly stored files. Configuration or model-version failures are quarantined; correct the registered configuration, then retry the job through the protected job endpoint. Never copy signed asset URLs into logs, reports, dashboards, or support tickets.

For a code-only update, rebuild and recreate the API and worker without deleting volumes:

```powershell
docker compose up -d --build api worker
docker compose exec -T api alembic current
docker compose exec -T api python -m scripts.run_fra_jobs --max-jobs 20
```

Do not use `docker compose down -v` during an update; it removes the database and private-upload volumes.

## Spatial reference updates

Every administrative, village, forest, water, groundwater, infrastructure, and cadastral import carries `source`, `source_version`, and `source_record_id`. Review invalid/repaired/duplicate counts, CRS, authority, licence, and classification before publication. Synthetic sources must remain visibly labeled and are prohibited in authoritative user-facing deployments.

## Incident response

Rotate `AUTH_SECRET` and cloud credentials after suspected exposure, invalidate active tokens, preserve append-only audit evidence, isolate affected storage, notify the privacy/security contact, and restore only from a verified clean backup.

Disable the affected adapter if a satellite source, analyser, or DSS rule set is found to be incorrect. Preserve its versioned inputs and outputs, mark the rule inactive or stop submitting the scene, and route affected cases to authorized human review. Do not rewrite prior evidence or recommendations.
