"""Run the durable FRA processing worker in bounded or continuous mode."""

import argparse
import json
import os
import signal
import socket
import threading
import uuid

from app.config import get_settings
from app.db.session import get_session_factory
from app.services.processing_jobs import run_one_job


def _worker_identity(worker_id: str | None) -> str:
    if worker_id and worker_id.strip():
        return worker_id.strip()
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}-fra-worker"


def _run_one(factory, *, identity: str):
    settings = get_settings()
    with factory() as session:
        return run_one_job(
            session,
            worker_id=identity,
            lease_seconds=settings.job_lease_seconds,
            retry_base_seconds=settings.job_retry_base_seconds,
            retry_max_seconds=settings.job_retry_max_seconds,
            heartbeat_session_factory=factory,
            heartbeat_interval_seconds=settings.job_heartbeat_seconds,
        )


def run_jobs(*, max_jobs: int, worker_id: str | None = None) -> dict:
    if max_jobs < 1:
        raise ValueError("max_jobs must be at least one.")
    identity = _worker_identity(worker_id)
    processed: list[dict] = []
    factory = get_session_factory()
    for _ in range(max_jobs):
        job = _run_one(factory, identity=identity)
        if job is None:
            break
        processed.append(
            {
                "id": str(job.id),
                "task_type": job.task_type,
                "state": job.state,
                "attempts": job.attempts,
                "error_code": job.error_code,
            }
        )
    return {"worker_id": identity, "processed_count": len(processed), "jobs": processed}


def run_forever(*, worker_id: str | None = None, stop_event=None) -> int:
    """Poll continuously until SIGINT/SIGTERM, using a fresh session for each job."""

    identity = _worker_identity(worker_id)
    settings = get_settings()
    factory = get_session_factory()
    stopping = stop_event or threading.Event()
    while not stopping.is_set():
        try:
            job = _run_one(factory, identity=identity)
        except Exception as error:
            print(
                json.dumps(
                    {
                        "worker_id": identity,
                        "state": "poll_failed",
                        "error_type": type(error).__name__,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            stopping.wait(settings.job_poll_seconds)
            continue
        if job is None:
            stopping.wait(settings.job_poll_seconds)
            continue
        print(
            json.dumps(
                {
                    "worker_id": identity,
                    "job_id": str(job.id),
                    "task_type": job.task_type,
                    "state": job.state,
                    "attempts": job.attempts,
                    "error_code": job.error_code,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    limit = parser.add_mutually_exclusive_group()
    limit.add_argument("--once", action="store_true", help="Process at most one job.")
    limit.add_argument("--max-jobs", type=int, default=1, help="Maximum jobs to process.")
    limit.add_argument("--forever", action="store_true", help="Poll until SIGINT or SIGTERM.")
    parser.add_argument("--worker-id")
    args = parser.parse_args()
    if args.forever:
        stop_event = threading.Event()

        def stop_worker(_signum, _frame):
            stop_event.set()

        signal.signal(signal.SIGINT, stop_worker)
        signal.signal(signal.SIGTERM, stop_worker)
        return run_forever(worker_id=args.worker_id, stop_event=stop_event)
    report = run_jobs(max_jobs=1 if args.once else args.max_jobs, worker_id=args.worker_id)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
