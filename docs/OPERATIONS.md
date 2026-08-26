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

After restoring an FRA-enabled database, confirm that `alembic heads` reports `20260826_0003`, then reconcile counts for FRA claims, decisions, geometry/title versions, evidence, satellite observations, rule sets, recommendations, and audit events. Never repair history by deleting an earlier decision or version.

## Cadastral updates

Every import carries `source`, `source_version`, and `source_record_id`. Review the import report, especially invalid/repaired/duplicate counts, before publication. Synthetic sources must remain visibly labeled and are prohibited in authoritative user-facing deployments.

## Incident response

Rotate `AUTH_SECRET` and cloud credentials after suspected exposure, invalidate active tokens, preserve append-only audit evidence, isolate affected storage, notify the privacy/security contact, and restore only from a verified clean backup.

Disable the affected adapter if a satellite source, analyser, or DSS rule set is found to be incorrect. Preserve its versioned inputs and outputs, mark the rule inactive or stop submitting the scene, and route affected cases to authorized human review. Do not rewrite prior evidence or recommendations.
