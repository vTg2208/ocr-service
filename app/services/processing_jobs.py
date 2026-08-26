"""Database-backed, bounded processing job orchestration."""

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.fra_completion_models import ProcessingJob


class JobStateError(RuntimeError):
    pass


class JobExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retriable: bool):
        self.code = code
        self.retriable = retriable
        super().__init__(message)


def enqueue_job(
    session,
    *,
    task_type: str,
    entity_type: str,
    entity_id,
    actor_id,
    idempotency_key: str,
    payload: dict,
    max_attempts: int = 3,
) -> ProcessingJob:
    task_type = task_type.strip()
    entity_type = entity_type.strip()
    idempotency_key = idempotency_key.strip()
    if not task_type or not entity_type or not idempotency_key:
        raise ValueError("Task type, entity type, and idempotency key are required.")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least one.")
    existing = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.task_type == task_type,
            ProcessingJob.entity_id == entity_id,
            ProcessingJob.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing
    job = ProcessingJob(
        task_type=task_type,
        entity_type=entity_type,
        entity_id=entity_id,
        requested_by=actor_id,
        idempotency_key=idempotency_key,
        payload_json=dict(payload),
        max_attempts=max_attempts,
    )
    session.add(job)
    session.flush()
    return job


def claim_next_job(session, *, worker_id: str) -> ProcessingJob | None:
    worker_id = worker_id.strip()
    if not worker_id:
        raise ValueError("A worker ID is required.")
    now = datetime.now(timezone.utc)
    statement = (
        select(ProcessingJob)
        .where(
            ProcessingJob.state == "queued",
            ProcessingJob.available_at <= now,
        )
        .order_by(ProcessingJob.created_at, ProcessingJob.id)
        .limit(1)
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    job = session.scalar(statement)
    if job is None:
        return None
    job.state = "running"
    job.attempts += 1
    job.worker_id = worker_id
    job.started_at = now
    job.completed_at = None
    job.error_code = None
    job.error_message = None
    session.flush()
    return job


def complete_job(session, job: ProcessingJob, *, result: dict) -> ProcessingJob:
    if job.state != "running":
        raise JobStateError("Only a running job can be completed.")
    job.state = "completed"
    job.result_json = dict(result)
    job.error_code = None
    job.error_message = None
    job.completed_at = datetime.now(timezone.utc)
    session.flush()
    return job


def fail_job(
    session,
    job: ProcessingJob,
    *,
    code: str,
    message: str,
    retriable: bool,
) -> ProcessingJob:
    if job.state != "running":
        raise JobStateError("Only a running job can fail.")
    if retriable and job.attempts < job.max_attempts:
        job.state = "queued"
        job.worker_id = None
        job.started_at = None
    else:
        job.state = "failed" if retriable else "quarantined"
        job.completed_at = datetime.now(timezone.utc)
    job.result_json = {}
    job.error_code = code.strip() or "processing_error"
    job.error_message = message
    session.flush()
    return job


def run_one_job(session, *, worker_id: str, handlers: dict | None = None) -> ProcessingJob | None:
    """Claim and run one job, rolling back every partial domain mutation on failure."""

    if handlers is None:
        from app.services.fra_job_handlers import JOB_HANDLERS

        handlers = JOB_HANDLERS
    job = claim_next_job(session, worker_id=worker_id)
    if job is None:
        return None
    job_id = job.id
    session.commit()
    handler = handlers.get(job.task_type)
    if handler is None:
        error = JobExecutionError(
            "handler_unavailable",
            f"No handler is registered for task type {job.task_type!r}.",
            retriable=False,
        )
    else:
        try:
            result = handler(session, job)
            if not isinstance(result, dict):
                raise JobExecutionError(
                    "invalid_handler_result",
                    "A job handler must return an object result.",
                    retriable=False,
                )
            complete_job(session, job, result=result)
            session.commit()
            return job
        except JobExecutionError as exc:
            error = exc
        except Exception as exc:  # Handler failures are recorded without partial writes.
            error = JobExecutionError("handler_error", str(exc), retriable=True)
    session.rollback()
    persisted_job = session.get(ProcessingJob, job_id)
    if persisted_job is None:
        raise JobStateError("Claimed job disappeared before failure could be recorded.")
    fail_job(
        session,
        persisted_job,
        code=error.code,
        message=str(error),
        retriable=error.retriable,
    )
    session.commit()
    return persisted_job
