"""Database-backed processing jobs with leases, recovery, retries, and fencing."""

from datetime import datetime, timedelta, timezone
import threading
import uuid

from sqlalchemy import or_, select

from app.db.fra_completion_models import ProcessingJob


class JobStateError(RuntimeError):
    pass


class JobExecutionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retriable: bool):
        self.code = code
        self.retriable = retriable
        super().__init__(message)


DEFAULT_LEASE_SECONDS = 300
DEFAULT_RETRY_BASE_SECONDS = 15
DEFAULT_RETRY_MAX_SECONDS = 900
MAX_FAILURE_MESSAGE_LENGTH = 4000


def _now(value: datetime | None = None) -> datetime:
    return value or datetime.now(timezone.utc)


def _retry_delay(attempt: int, *, base_seconds: int, max_seconds: int) -> int:
    if base_seconds < 0 or max_seconds < 0:
        raise ValueError("Retry delays cannot be negative.")
    return min(base_seconds * (2 ** max(attempt - 1, 0)), max_seconds)


def _record_failure(
    job: ProcessingJob,
    *,
    code: str,
    message: str,
    retriable: bool,
    failed_at: datetime,
    worker_id: str | None = None,
) -> None:
    clean_code = code.strip() or "processing_error"
    clean_message = str(message).strip()[:MAX_FAILURE_MESSAGE_LENGTH]
    history = list(job.failure_history_json or [])
    history.append(
        {
            "attempt": job.attempts,
            "code": clean_code,
            "message": clean_message,
            "retriable": retriable,
            "worker_id": worker_id if worker_id is not None else job.worker_id,
            "failed_at": failed_at.isoformat(),
        }
    )
    job.failure_history_json = history
    job.error_code = clean_code
    job.error_message = clean_message


def _clear_lease(job: ProcessingJob) -> None:
    job.worker_id = None
    job.lease_token = None
    job.lease_expires_at = None
    job.heartbeat_at = None


def _lock_current_job(session, job: ProcessingJob) -> ProcessingJob:
    statement = select(ProcessingJob).where(ProcessingJob.id == job.id)
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        statement = statement.with_for_update()
    current = session.scalar(statement.execution_options(populate_existing=True))
    if current is None:
        raise JobStateError("Processing job no longer exists.")
    return current


def _require_active_lease(job: ProcessingJob, lease_token: str | None) -> None:
    if lease_token is not None and job.lease_token != lease_token:
        raise JobStateError("The worker lease is no longer active for this job.")


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


def recover_expired_jobs(
    session,
    *,
    now: datetime | None = None,
    retry_base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
    retry_max_seconds: int = DEFAULT_RETRY_MAX_SECONDS,
    limit: int = 100,
) -> list[uuid.UUID]:
    """Recover jobs abandoned by workers whose leases have expired."""

    if limit < 1:
        raise ValueError("Recovery limit must be at least one.")
    current_time = _now(now)
    statement = (
        select(ProcessingJob)
        .where(
            ProcessingJob.state == "running",
            or_(
                ProcessingJob.lease_expires_at.is_(None),
                ProcessingJob.lease_expires_at <= current_time,
            ),
        )
        .order_by(ProcessingJob.lease_expires_at, ProcessingJob.id)
        .limit(limit)
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    recovered = list(session.scalars(statement).all())
    for job in recovered:
        abandoned_worker = job.worker_id
        _record_failure(
            job,
            code="worker_lease_expired",
            message="The processing worker stopped renewing its lease before completion.",
            retriable=True,
            failed_at=current_time,
            worker_id=abandoned_worker,
        )
        _clear_lease(job)
        job.result_json = {}
        if job.attempts < job.max_attempts:
            job.state = "queued"
            delay = _retry_delay(
                job.attempts,
                base_seconds=retry_base_seconds,
                max_seconds=retry_max_seconds,
            )
            job.available_at = current_time + timedelta(seconds=delay)
            job.started_at = None
            job.completed_at = None
        else:
            job.state = "failed"
            job.completed_at = current_time
    session.flush()
    return [job.id for job in recovered]


def claim_next_job(
    session,
    *,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
    retry_base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
    retry_max_seconds: int = DEFAULT_RETRY_MAX_SECONDS,
) -> ProcessingJob | None:
    worker_id = worker_id.strip()
    if not worker_id:
        raise ValueError("A worker ID is required.")
    if lease_seconds < 1:
        raise ValueError("Lease duration must be at least one second.")
    current_time = _now(now)
    recover_expired_jobs(
        session,
        now=current_time,
        retry_base_seconds=retry_base_seconds,
        retry_max_seconds=retry_max_seconds,
    )
    statement = (
        select(ProcessingJob)
        .where(
            ProcessingJob.state == "queued",
            ProcessingJob.available_at <= current_time,
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
    job.lease_token = str(uuid.uuid4())
    job.started_at = current_time
    job.heartbeat_at = current_time
    job.lease_expires_at = current_time + timedelta(seconds=lease_seconds)
    job.completed_at = None
    job.error_code = None
    job.error_message = None
    session.flush()
    return job


def heartbeat_job(
    session,
    *,
    job_id,
    worker_id: str,
    lease_token: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> ProcessingJob:
    if lease_seconds < 1:
        raise ValueError("Lease duration must be at least one second.")
    statement = select(ProcessingJob).where(ProcessingJob.id == job_id)
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        statement = statement.with_for_update()
    job = session.scalar(statement.execution_options(populate_existing=True))
    if (
        job is None
        or job.state != "running"
        or job.worker_id != worker_id
        or job.lease_token != lease_token
    ):
        raise JobStateError("The worker lease is no longer active for this job.")
    current_time = _now(now)
    job.heartbeat_at = current_time
    job.lease_expires_at = current_time + timedelta(seconds=lease_seconds)
    session.flush()
    return job


def complete_job(
    session,
    job: ProcessingJob,
    *,
    result: dict,
    lease_token: str | None = None,
) -> ProcessingJob:
    job = _lock_current_job(session, job)
    _require_active_lease(job, lease_token)
    if job.state != "running":
        raise JobStateError("Only a running job can be completed.")
    job.state = "completed"
    job.result_json = dict(result)
    job.error_code = None
    job.error_message = None
    job.completed_at = datetime.now(timezone.utc)
    _clear_lease(job)
    session.flush()
    return job


def fail_job(
    session,
    job: ProcessingJob,
    *,
    code: str,
    message: str,
    retriable: bool,
    lease_token: str | None = None,
    now: datetime | None = None,
    retry_base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
    retry_max_seconds: int = DEFAULT_RETRY_MAX_SECONDS,
) -> ProcessingJob:
    job = _lock_current_job(session, job)
    _require_active_lease(job, lease_token)
    if job.state != "running":
        raise JobStateError("Only a running job can fail.")
    current_time = _now(now)
    _record_failure(
        job,
        code=code,
        message=message,
        retriable=retriable,
        failed_at=current_time,
    )
    if retriable and job.attempts < job.max_attempts:
        job.state = "queued"
        job.available_at = current_time + timedelta(
            seconds=_retry_delay(
                job.attempts,
                base_seconds=retry_base_seconds,
                max_seconds=retry_max_seconds,
            )
        )
        _clear_lease(job)
        job.started_at = None
    else:
        job.state = "failed" if retriable else "quarantined"
        job.completed_at = current_time
        _clear_lease(job)
    job.result_json = {}
    session.flush()
    return job


def retry_job(session, job: ProcessingJob) -> ProcessingJob:
    if job.state not in {"failed", "quarantined"}:
        raise JobStateError("Only a failed or quarantined job can be retried.")
    job.state = "queued"
    job.attempts = 0
    _clear_lease(job)
    job.started_at = None
    job.completed_at = None
    job.error_code = None
    job.error_message = None
    job.result_json = {}
    job.available_at = datetime.now(timezone.utc)
    session.flush()
    return job


class _LeaseHeartbeat:
    def __init__(
        self,
        *,
        session_factory,
        job_id,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
        interval_seconds: float,
    ):
        self.session_factory = session_factory
        self.job_id = job_id
        self.worker_id = worker_id
        self.lease_token = lease_token
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self.stop_event = threading.Event()
        self.lost_lease = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=max(self.interval_seconds * 2, 1))

    def _run(self):
        while not self.stop_event.wait(self.interval_seconds):
            try:
                with self.session_factory() as heartbeat_session:
                    heartbeat_job(
                        heartbeat_session,
                        job_id=self.job_id,
                        worker_id=self.worker_id,
                        lease_token=self.lease_token,
                        lease_seconds=self.lease_seconds,
                    )
                    heartbeat_session.commit()
            except Exception:
                self.lost_lease = True
                return


def run_one_job(
    session,
    *,
    worker_id: str,
    handlers: dict | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    retry_base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
    retry_max_seconds: int = DEFAULT_RETRY_MAX_SECONDS,
    heartbeat_session_factory=None,
    heartbeat_interval_seconds: float = 30,
) -> ProcessingJob | None:
    """Claim and run one job, rolling back every partial domain mutation on failure."""

    if handlers is None:
        from app.services.fra_job_handlers import JOB_HANDLERS

        handlers = JOB_HANDLERS
    job = claim_next_job(
        session,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        retry_base_seconds=retry_base_seconds,
        retry_max_seconds=retry_max_seconds,
    )
    if job is None:
        return None
    job_id = job.id
    lease_token = job.lease_token
    session.commit()
    heartbeat = None
    if heartbeat_session_factory is not None:
        if heartbeat_interval_seconds <= 0 or heartbeat_interval_seconds >= lease_seconds:
            raise ValueError("Heartbeat interval must be positive and shorter than the lease.")
        heartbeat = _LeaseHeartbeat(
            session_factory=heartbeat_session_factory,
            job_id=job_id,
            worker_id=worker_id,
            lease_token=lease_token,
            lease_seconds=lease_seconds,
            interval_seconds=heartbeat_interval_seconds,
        )
        heartbeat.start()
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
            if heartbeat is not None:
                heartbeat.stop()
                if heartbeat.lost_lease:
                    raise JobStateError("The worker could not renew its job lease.")
            complete_job(session, job, result=result, lease_token=lease_token)
            session.commit()
            return job
        except JobExecutionError as exc:
            error = exc
        except Exception as exc:  # Handler failures are recorded without partial writes.
            error = JobExecutionError("handler_error", str(exc), retriable=True)
    if heartbeat is not None:
        heartbeat.stop()
    session.rollback()
    persisted_job = session.get(ProcessingJob, job_id)
    if persisted_job is None:
        raise JobStateError("Claimed job disappeared before failure could be recorded.")
    try:
        fail_job(
            session,
            persisted_job,
            code=error.code,
            message=str(error),
            retriable=error.retriable,
            lease_token=lease_token,
            retry_base_seconds=retry_base_seconds,
            retry_max_seconds=retry_max_seconds,
        )
        session.commit()
        return persisted_job
    except JobStateError:
        session.rollback()
        current = session.get(ProcessingJob, job_id)
        if current is None:
            raise JobStateError("Claimed job disappeared before failure could be recorded.")
        return current
