import unittest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.fra_completion_models import ProcessingJob
from app.db.models import User
from app.services.processing_jobs import (
    JobExecutionError,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    heartbeat_job,
    recover_expired_jobs,
    run_one_job,
)


class ProcessingJobTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)

    def tearDown(self):
        self.engine.dispose()

    def _staff(self, session):
        staff = User(external_id=str(uuid.uuid4()), display_name="TN staff", role="user")
        session.add(staff)
        session.flush()
        return staff

    def test_worker_claims_a_job_once_and_completes_it(self):
        with self.sessions() as session:
            staff = self._staff(session)
            job = enqueue_job(
                session,
                task_type="archive_extract",
                entity_type="archive_record",
                entity_id=uuid.uuid4(),
                actor_id=staff.id,
                idempotency_key="extract-1",
                payload={},
            )
            session.commit()

            claimed = claim_next_job(session, worker_id="worker-a")
            self.assertEqual(claimed.id, job.id)
            self.assertEqual(claimed.state, "running")
            self.assertEqual(claimed.attempts, 1)
            complete_job(session, claimed, result={"run_id": "x"})
            self.assertIsNone(claim_next_job(session, worker_id="worker-b"))

    def test_enqueue_is_idempotent_for_same_task_entity_and_key(self):
        with self.sessions() as session:
            staff = self._staff(session)
            entity_id = uuid.uuid4()
            first = enqueue_job(
                session,
                task_type="archive_extract",
                entity_type="archive_record",
                entity_id=entity_id,
                actor_id=staff.id,
                idempotency_key="same",
                payload={"attempt": 1},
            )
            second = enqueue_job(
                session,
                task_type="archive_extract",
                entity_type="archive_record",
                entity_id=entity_id,
                actor_id=staff.id,
                idempotency_key="same",
                payload={"attempt": 2},
            )

            self.assertEqual(first.id, second.id)
            self.assertEqual(second.payload_json, {"attempt": 1})

    def test_permanent_failure_is_quarantined_without_partial_result(self):
        with self.sessions() as session:
            staff = self._staff(session)
            job = enqueue_job(
                session,
                task_type="archive_extract",
                entity_type="archive_record",
                entity_id=uuid.uuid4(),
                actor_id=staff.id,
                idempotency_key="bad-manifest",
                payload={},
            )
            session.flush()
            claim_next_job(session, worker_id="worker-a")

            fail_job(
                session,
                job,
                code="invalid_manifest",
                message="Bad labels",
                retriable=False,
            )

            self.assertEqual(job.state, "quarantined")
            self.assertEqual(job.result_json, {})

    def test_retriable_failure_requeues_then_exhausts_attempts(self):
        with self.sessions() as session:
            staff = self._staff(session)
            job = enqueue_job(
                session,
                task_type="asset_inference",
                entity_type="village",
                entity_id=uuid.uuid4(),
                actor_id=staff.id,
                idempotency_key="retry-me",
                payload={},
                max_attempts=2,
            )
            claim_next_job(session, worker_id="worker-a")
            fail_job(
                session,
                job,
                code="provider_down",
                message="Offline",
                retriable=True,
                retry_base_seconds=0,
                retry_max_seconds=0,
            )
            self.assertEqual(job.state, "queued")
            claim_next_job(session, worker_id="worker-b")
            fail_job(
                session,
                job,
                code="provider_down",
                message="Offline",
                retriable=True,
                retry_base_seconds=0,
                retry_max_seconds=0,
            )
            self.assertEqual(job.state, "failed")

    def test_claim_creates_a_renewable_lease(self):
        now = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)
        with self.sessions() as session:
            staff = self._staff(session)
            job = enqueue_job(
                session,
                task_type="archive_extract",
                entity_type="archive_record",
                entity_id=uuid.uuid4(),
                actor_id=staff.id,
                idempotency_key="leased-job",
                payload={},
            )
            job.available_at = now
            claimed = claim_next_job(
                session, worker_id="worker-a", lease_seconds=60, now=now
            )

            self.assertEqual(claimed.id, job.id)
            self.assertIsNotNone(claimed.lease_token)
            self.assertEqual(claimed.heartbeat_at, now)
            self.assertEqual(claimed.lease_expires_at, now + timedelta(seconds=60))

            heartbeat_job(
                session,
                job_id=job.id,
                worker_id="worker-a",
                lease_token=claimed.lease_token,
                lease_seconds=60,
                now=now + timedelta(seconds=30),
            )
            self.assertEqual(job.heartbeat_at, now + timedelta(seconds=30))
            self.assertEqual(job.lease_expires_at, now + timedelta(seconds=90))

    def test_expired_worker_lease_requeues_a_stranded_job_with_backoff(self):
        now = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)
        with self.sessions() as session:
            staff = self._staff(session)
            job = enqueue_job(
                session,
                task_type="archive_extract",
                entity_type="archive_record",
                entity_id=uuid.uuid4(),
                actor_id=staff.id,
                idempotency_key="stranded-job",
                payload={},
            )
            job.available_at = now
            claim_next_job(session, worker_id="dead-worker", lease_seconds=10, now=now)
            recovered = recover_expired_jobs(
                session,
                now=now + timedelta(seconds=11),
                retry_base_seconds=15,
                retry_max_seconds=60,
            )

            self.assertEqual(recovered, [job.id])
            self.assertEqual(job.state, "queued")
            self.assertIsNone(job.worker_id)
            self.assertIsNone(job.lease_token)
            self.assertEqual(job.error_code, "worker_lease_expired")
            self.assertEqual(job.available_at, now + timedelta(seconds=26))
            self.assertEqual(job.failure_history_json[-1]["worker_id"], "dead-worker")

    def test_expired_lease_exhaustion_marks_job_failed(self):
        now = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)
        with self.sessions() as session:
            staff = self._staff(session)
            job = enqueue_job(
                session,
                task_type="archive_extract",
                entity_type="archive_record",
                entity_id=uuid.uuid4(),
                actor_id=staff.id,
                idempotency_key="stranded-final-attempt",
                payload={},
                max_attempts=1,
            )
            job.available_at = now
            claim_next_job(session, worker_id="dead-worker", lease_seconds=10, now=now)
            recover_expired_jobs(session, now=now + timedelta(seconds=11))

            self.assertEqual(job.state, "failed")
            self.assertEqual(job.error_code, "worker_lease_expired")
            self.assertEqual(job.completed_at, now + timedelta(seconds=11))

    def test_prelease_running_job_is_recovered_instead_of_remaining_stranded(self):
        now = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)
        with self.sessions() as session:
            staff = self._staff(session)
            job = enqueue_job(
                session,
                task_type="archive_extract",
                entity_type="archive_record",
                entity_id=uuid.uuid4(),
                actor_id=staff.id,
                idempotency_key="prelease-running-job",
                payload={},
            )
            job.state = "running"
            job.attempts = 1
            job.worker_id = "legacy-worker"
            job.lease_token = None
            job.lease_expires_at = None

            recovered = recover_expired_jobs(
                session,
                now=now,
                retry_base_seconds=0,
                retry_max_seconds=0,
            )

            self.assertEqual(recovered, [job.id])
            self.assertEqual(job.state, "queued")
            self.assertEqual(job.error_code, "worker_lease_expired")

    def test_retriable_failures_use_bounded_exponential_backoff_and_history(self):
        now = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)
        with self.sessions() as session:
            staff = self._staff(session)
            job = enqueue_job(
                session,
                task_type="asset_inference",
                entity_type="village",
                entity_id=uuid.uuid4(),
                actor_id=staff.id,
                idempotency_key="backoff-job",
                payload={},
                max_attempts=3,
            )
            job.available_at = now
            claim_next_job(session, worker_id="worker-a", now=now)
            fail_job(
                session,
                job,
                code="provider_down",
                message="Offline",
                retriable=True,
                now=now,
                retry_base_seconds=10,
                retry_max_seconds=15,
            )
            self.assertEqual(job.available_at, now + timedelta(seconds=10))
            self.assertEqual(job.failure_history_json[-1]["attempt"], 1)

            claim_next_job(session, worker_id="worker-b", now=now + timedelta(seconds=10))
            fail_job(
                session,
                job,
                code="provider_down",
                message="Still offline",
                retriable=True,
                now=now + timedelta(seconds=10),
                retry_base_seconds=10,
                retry_max_seconds=15,
            )
            self.assertEqual(job.available_at, now + timedelta(seconds=25))
            self.assertEqual(len(job.failure_history_json), 2)

    def test_stale_worker_cannot_complete_a_reclaimed_job(self):
        now = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)
        with self.sessions() as session:
            staff = self._staff(session)
            job = enqueue_job(
                session,
                task_type="archive_extract",
                entity_type="archive_record",
                entity_id=uuid.uuid4(),
                actor_id=staff.id,
                idempotency_key="fenced-job",
                payload={},
            )
            job.available_at = now
            first = claim_next_job(session, worker_id="worker-a", lease_seconds=10, now=now)
            old_token = first.lease_token
            recover_expired_jobs(
                session,
                now=now + timedelta(seconds=11),
                retry_base_seconds=0,
                retry_max_seconds=0,
            )
            second = claim_next_job(
                session, worker_id="worker-b", lease_seconds=60, now=now + timedelta(seconds=11)
            )
            self.assertNotEqual(second.lease_token, old_token)

            with self.assertRaisesRegex(Exception, "lease"):
                complete_job(session, second, result={"stale": True}, lease_token=old_token)
            self.assertEqual(job.state, "running")

    def test_run_one_job_rolls_back_partial_handler_rows_before_failure_state(self):
        with self.sessions() as session:
            staff = self._staff(session)
            job = enqueue_job(
                session,
                task_type="test_failure",
                entity_type="subject",
                entity_id=uuid.uuid4(),
                actor_id=staff.id,
                idempotency_key="atomic-1",
                payload={},
            )
            session.commit()

            def failing_handler(handler_session, _job):
                handler_session.add(
                    User(external_id="partial-user", display_name="Partial", role="user")
                )
                handler_session.flush()
                raise JobExecutionError("invalid_manifest", "Bad labels", retriable=False)

            result = run_one_job(
                session,
                worker_id="worker-a",
                handlers={"test_failure": failing_handler},
            )

            self.assertEqual(result.state, "quarantined")
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(User).where(User.external_id == "partial-user")
                ),
                0,
            )


if __name__ == "__main__":
    unittest.main()
