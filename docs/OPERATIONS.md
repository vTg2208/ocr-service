# Land registry operations

## Monitoring and alerts

Probe `/health` for process liveness and `/health/ready` for database readiness. Alert when readiness fails twice, 5xx responses exceed 2% for five minutes, OCR latency exceeds the service objective, storage or ClamAV is unavailable, conflict-queue age exceeds the review target, disk/database capacity exceeds 80%, or a scheduled backup/restore verification fails.

Logs are structured as key/value access events with method, path, status, duration, and request ID. Forward them over encrypted transport and redact authorization headers and document/OCR content.

For the FRA domain, also alert on repeated satellite-provider `503` responses, abnormal DSS `insufficient_data` rates, overdue reviewer transitions, failed audit writes, and PostGIS spatial-query errors. Satellite and DSS alerts indicate operational state only; they must not be interpreted as legal or eligibility decisions.

## Backup and restore

Run `python scripts/postgres_backup.py backups/ocr-land.dump` from an environment with `DATABASE_URL` and `pg_dump`. Encrypt backup storage and apply the approved retention policy.

At least monthly, restore into an isolated non-production database:

```text
python scripts/postgres_restore.py backups/ocr-land.dump --confirm-destructive-restore
alembic upgrade head
python -m pytest tests/test_migrations.py tests/test_land_api.py
```

Record the restore date, duration, row counts, PostGIS geometry validity check, and reviewer. Never test restoration against production.

After restoring an FRA-enabled database, confirm that `alembic heads` reports `20260902_0005`, then reconcile counts for FRA claims, decisions, geometry/title versions, intake/archive records, spatial imports, imagery scenes/artifacts, fact snapshots, scheme catalogue versions, processing jobs, model versions, assets, referrals, reports, and audit events. Never repair history by deleting an earlier decision or version.

## Tamil Nadu FRA sample data and workers

Apply migrations before using the completed FRA workspaces:

```text
python -m alembic upgrade head
python -m scripts.seed_tamil_nadu_fra_demo
python -m scripts.seed_tamil_nadu_fra_demo
python -m scripts.run_fra_jobs --max-jobs 20
```

The seed is idempotent: the first run reports created records and the second reports `created: 0`. Everything it inserts is invented, visibly synthetic Tamil Nadu sample data. Do not run this seed in an authoritative database. The UI is protected at `/fra`; Dashboard, Archive, Cases, Atlas, Assets, DSS Planner, and Reports share the Tamil Nadu context bar.

The worker uses durable `processing_jobs` rows and bounded retries. Alert on quarantined jobs, repeated failures, or a growing oldest-queued age. A missing or inactive model is an unavailable-model condition, not a successful inference. See `docs/MODEL_ADAPTERS.md` before attaching trained weights.

Historical discovery is bounded by `STAC_ENDPOINT`, `STAC_ALLOWED_HOSTS`, `STAC_ALLOWED_COLLECTIONS`, `STAC_TIMEOUT_SECONDS`, `STAC_MAX_PAGES`, `STAC_MAX_RESULTS`, and `STAC_MAX_CLOUD`. Keep the endpoint and every pagination host allow-listed. Provider failures are retryable and roll back scene/artifact rows and newly stored files. Configuration or model-version failures are quarantined; correct the registered configuration, then retry the job through the protected job endpoint. Never copy signed asset URLs into logs, reports, dashboards, or support tickets.

For a code-only update, rebuild and recreate the API container without deleting volumes:

```powershell
docker compose up -d --build api
docker compose exec -T api alembic current
docker compose exec -T api python -m scripts.run_fra_jobs --max-jobs 20
```

Do not use `docker compose down -v` during an update; it removes the database and private-upload volumes.

## Cadastral updates

Every import carries `source`, `source_version`, and `source_record_id`. Review the import report, especially invalid/repaired/duplicate counts, before publication. Synthetic sources must remain visibly labeled and are prohibited in authoritative user-facing deployments.

## Incident response

Rotate `AUTH_SECRET` and cloud credentials after suspected exposure, invalidate active tokens, preserve append-only audit evidence, isolate affected storage, notify the privacy/security contact, and restore only from a verified clean backup.

Disable the affected adapter if a satellite source, analyser, or DSS rule set is found to be incorrect. Preserve its versioned inputs and outputs, mark the rule inactive or stop submitting the scene, and route affected cases to authorized human review. Do not rewrite prior evidence or recommendations.
