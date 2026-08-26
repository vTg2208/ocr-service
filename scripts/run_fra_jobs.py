"""Run a bounded number of queued FRA processing jobs."""

import argparse
import json
import socket

from app.db.session import get_session_factory
from app.services.processing_jobs import run_one_job


def run_jobs(*, max_jobs: int, worker_id: str | None = None) -> dict:
    if max_jobs < 1:
        raise ValueError("max_jobs must be at least one.")
    identity = worker_id or f"{socket.gethostname()}-fra-worker"
    processed: list[dict] = []
    with get_session_factory()() as session:
        for _ in range(max_jobs):
            job = run_one_job(session, worker_id=identity)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    limit = parser.add_mutually_exclusive_group()
    limit.add_argument("--once", action="store_true", help="Process at most one job.")
    limit.add_argument("--max-jobs", type=int, default=1, help="Maximum jobs to process.")
    parser.add_argument("--worker-id")
    args = parser.parse_args()
    report = run_jobs(max_jobs=1 if args.once else args.max_jobs, worker_id=args.worker_id)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
