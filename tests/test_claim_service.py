import unittest
import uuid

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import AuditEvent, Claim, ClaimConflict, Document, OCRResult, Parcel, User
from app.services.claim_service import ClaimService, ordered_claim_pair


def polygon(x1=0, y1=0, x2=1, y2=1):
    return {"type": "MultiPolygon", "coordinates": [[[[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]]]}


class ClaimServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            self.user_a = User(external_id="user-a", role="user")
            self.user_b = User(external_id="user-b", role="user")
            self.parcel = Parcel(
                state="Tamil Nadu", district="Thanjavur", taluk="Kumbakonam",
                village="Example Village", survey_number="701", subdivision_number="4B",
                official_area_sqm=1200, geometry=polygon(), source="Synthetic development data",
            )
            session.add_all([self.user_a, self.user_b, self.parcel]); session.flush()
            self.document_a = self._document(session, self.user_a, "a")
            self.document_b = self._document(session, self.user_b, "b")
            session.commit()
            for item in (self.user_a, self.user_b, self.parcel, self.document_a, self.document_b):
                session.refresh(item)
                setattr(self, f"{item.__class__.__name__.lower()}_{'a' if getattr(item, 'external_id', '').endswith('a') or getattr(item, 'original_filename', '').startswith('a') else 'b'}", item)
            self.user_a_id, self.user_b_id = self.user_a.id, self.user_b.id
            self.parcel_id = self.parcel.id
            self.document_a_id, self.document_b_id = self.document_a.id, self.document_b.id

    @staticmethod
    def _document(session, user, prefix):
        document = Document(
            uploaded_by=user.id, storage_key=f"private/{prefix}", original_filename=f"{prefix}.png",
            content_type="image/png", sha256=prefix * 64, ocr_status="completed", idempotency_key=prefix,
        )
        session.add(document); session.flush()
        session.add(OCRResult(
            document_id=document.id, raw_text="Survey 701/4B", overall_confidence=0.9,
            structured_result_json={"valid_parcel_ids": []}, extractor_version="test",
        ))
        return document

    def _mark_candidate(self, session, document_id):
        result = session.scalar(select(OCRResult).where(OCRResult.document_id == document_id))
        result.structured_result_json = {"valid_parcel_ids": [str(self.parcel_id)]}

    def test_orders_conflict_pair_deterministically(self):
        high, low = uuid.UUID(int=2), uuid.UUID(int=1)
        self.assertEqual(ordered_claim_pair(high, low), (low, high))

    def test_second_active_claim_creates_privacy_safe_same_parcel_conflict(self):
        with Session(self.engine) as session:
            self._mark_candidate(session, self.document_a_id)
            self._mark_candidate(session, self.document_b_id)
            service = ClaimService(session)
            first = service.submit(
                claimant_id=self.user_a_id, document_id=self.document_a_id, parcel_id=self.parcel_id,
                confirmed_fields={"document_area_sqm": 1200}, idempotency_key="claim-a", request_id="r1",
            )
            second = service.submit(
                claimant_id=self.user_b_id, document_id=self.document_b_id, parcel_id=self.parcel_id,
                confirmed_fields={"document_area_sqm": 1200}, idempotency_key="claim-b", request_id="r2",
            )
            session.commit()

        self.assertEqual(first["status"], "matched")
        self.assertEqual(second["status"], "conflicting")
        self.assertEqual(second["conflicts"][0]["type"], "same_parcel")
        self.assertNotIn("claimant_id", second["conflicts"][0])
        self.assertNotIn("document_id", second["conflicts"][0])

    def test_duplicate_idempotency_key_returns_same_claim_without_duplicate_conflict(self):
        with Session(self.engine) as session:
            self._mark_candidate(session, self.document_a_id)
            service = ClaimService(session)
            first = service.submit(
                claimant_id=self.user_a_id, document_id=self.document_a_id, parcel_id=self.parcel_id,
                confirmed_fields={}, idempotency_key="same", request_id="r1",
            )
            second = service.submit(
                claimant_id=self.user_a_id, document_id=self.document_a_id, parcel_id=self.parcel_id,
                confirmed_fields={}, idempotency_key="same", request_id="r2",
            )
            session.commit()
            count = session.scalar(select(func.count()).select_from(Claim))
        self.assertEqual(first["claim_id"], second["claim_id"])
        self.assertEqual(count, 1)

    def test_same_claimant_new_document_does_not_conflict_with_own_claim(self):
        with Session(self.engine) as session:
            self._mark_candidate(session, self.document_a_id)
            user = session.get(User, self.user_a_id)
            second_document = self._document(session, user, "c")
            session.flush()
            self._mark_candidate(session, second_document.id)
            service = ClaimService(session)
            first = service.submit(
                claimant_id=self.user_a_id, document_id=self.document_a_id,
                parcel_id=self.parcel_id, confirmed_fields={},
                idempotency_key="first-supporting-document", request_id="r1",
            )
            second = service.submit(
                claimant_id=self.user_a_id, document_id=second_document.id,
                parcel_id=self.parcel_id, confirmed_fields={},
                idempotency_key="second-supporting-document", request_id="r2",
            )
            session.commit()
            conflict_count = session.scalar(select(func.count()).select_from(ClaimConflict))

        self.assertEqual(first["status"], "matched")
        self.assertEqual(second["status"], "matched")
        self.assertEqual(second["conflicts"], [])
        self.assertEqual(conflict_count, 0)

    def test_rejects_document_owned_by_another_user_or_unresolved_parcel(self):
        with Session(self.engine) as session:
            service = ClaimService(session)
            with self.assertRaises(PermissionError):
                service.submit(
                    claimant_id=self.user_a_id, document_id=self.document_b_id, parcel_id=self.parcel_id,
                    confirmed_fields={}, idempotency_key="x", request_id="r",
                )
            self._mark_candidate(session, self.document_a_id)
            result = session.scalar(select(OCRResult).where(OCRResult.document_id == self.document_a_id))
            result.structured_result_json = {"valid_parcel_ids": []}
            with self.assertRaises(ValueError):
                service.submit(
                    claimant_id=self.user_a_id, document_id=self.document_a_id, parcel_id=self.parcel_id,
                    confirmed_fields={}, idempotency_key="y", request_id="r",
                )

    def test_rolls_back_claim_when_conflict_detector_fails(self):
        with Session(self.engine) as session:
            self._mark_candidate(session, self.document_a_id)
            service = ClaimService(
                session,
                conflict_detector=lambda *_, **__: (_ for _ in ()).throw(RuntimeError("boom")),
            )
            with self.assertRaises(RuntimeError):
                with session.begin_nested():
                    service.submit(
                        claimant_id=self.user_a_id, document_id=self.document_a_id, parcel_id=self.parcel_id,
                        confirmed_fields={}, idempotency_key="rollback", request_id="r",
                    )
            session.rollback()
            self.assertEqual(session.scalar(select(func.count()).select_from(Claim)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(AuditEvent)), 0)


if __name__ == "__main__":
    unittest.main()
