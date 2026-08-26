import unittest
import uuid

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
            fail_job(session, job, code="provider_down", message="Offline", retriable=True)
            self.assertEqual(job.state, "queued")
            claim_next_job(session, worker_id="worker-b")
            fail_job(session, job, code="provider_down", message="Offline", retriable=True)
            self.assertEqual(job.state, "failed")

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
