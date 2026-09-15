"""Real image OCR through the existing intake, review, and promotion path."""

from io import BytesIO
import os
import unittest
import uuid
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.fra_completion_models import FRAArchiveRecord, FRAFieldReview, ProcessingJob
from app.db.fra_models import FRADecision, FRATitle
from app.db.models import Document, User
from app.services.fra_archive import (
    create_archive_record, create_import_batch, process_archive_extraction,
    promote_archive_record, review_archive_record,
)
from app.services.fra_cases import case_detail, case_summary, list_cases
from app.services.fra_document_intake import ArchiveUpload, ingest_archive_batch
from app.services.fra_entity_extraction import TamilNaduFRAExtractor
from app.services.fra_job_handlers import get_job_handler
from tests.test_fra_document_intake import MemoryStorage, SelectiveScanner


def claim_image(lines: list[str]) -> bytes:
    font = ImageFont.truetype("arial.ttf" if os.name == "nt" else "DejaVuSans.ttf", 42)
    image = Image.new("RGB", (1800, 1200), "white")
    drawing = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        drawing.text((70, 50 + index * 95), line, font=font, fill="black")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class NewFRACommunityPromotionTests(unittest.TestCase):
    def test_cr_and_cfr_use_reviewed_gram_sabha_without_granting(self):
        for right_type in ("CR", "CFR"):
            with self.subTest(right_type=right_type):
                engine = create_engine("sqlite+pysqlite:///:memory:")
                Base.metadata.create_all(engine)
                try:
                    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
                        uploader = User(external_id="community-uploader", display_name="Uploader", role="user")
                        reviewer = User(external_id="community-reviewer", display_name="Reviewer", role="reviewer")
                        session.add_all([uploader, reviewer]); session.flush()
                        document = Document(
                            uploaded_by=uploader.id, storage_key=f"private/{uuid.uuid4()}.png",
                            original_filename="community-claim.png", content_type="image/png",
                            sha256="a" * 64, idempotency_key=str(uuid.uuid4()),
                        )
                        session.add(document); session.flush()
                        batch = create_import_batch(
                            session, source_label="District office", state="Tamil Nadu",
                            actor_id=uploader.id, idempotency_key=str(uuid.uuid4()),
                            synthetic=False, provenance={"source": "District office", "intake_kind": "new_claim"},
                        )
                        record = create_archive_record(
                            session, batch=batch, document_id=document.id,
                            legacy_reference=f"community-{right_type}", actor_id=uploader.id,
                        )
                        text = (
                            "FRA Claim Form\nClaimant: Kottur Community\nHolder Type: Community\n"
                            f"Right Type: {right_type}\nDistrict: Salem\nBlock: Yercaud\n"
                            "Village: Kottur\nGram Sabha Name: Kottur Gram Sabha\n"
                            "Claim Status: Granted\nTitle Number: SOURCE-ONLY-1"
                        )
                        run = process_archive_extraction(
                            session, record, extractor=TamilNaduFRAExtractor("tn-fra-labels-v1"),
                            manifest={"raw_text": text, "pages": [{"page_number": 1, "text": text, "confidence": 0.95}],
                                      "intake_kind": "new_claim"},
                            raw_text=text, actor_id=uploader.id,
                        )
                        reviewed = dict(run.standardized_json)
                        reviewed["gram_sabha_name"] = "Kottur Gram Sabha"
                        review_archive_record(session, record, reviewed_fields=reviewed,
                                              reviewer_id=reviewer.id, expected_revision=record.revision)
                        claim = promote_archive_record(session, record, actor_id=reviewer.id,
                                                       expected_revision=record.revision)
                        self.assertEqual(claim.claim_number, f"TN-FRA-{right_type}-{record.id.hex[:8].upper()}")
                        self.assertEqual(claim.status, "submitted")
                        self.assertEqual(claim.gram_sabha.name, "Kottur Gram Sabha")
                        self.assertEqual(claim.rights_holder.holder_type, "community")
                        self.assertEqual(len(claim.decisions), 0)
                        self.assertEqual(len(claim.titles), 0)
                        self.assertEqual(claim.document_id, document.id)
                finally:
                    engine.dispose()


@unittest.skipUnless(os.getenv("FRA_REAL_OCR_TEST") == "1", "Run with FRA_REAL_OCR_TEST=1 to exercise PaddleOCR")
class NewFRAClaimImageFlowTests(unittest.TestCase):
    def test_image_upload_ocr_review_approval_creates_linked_submitted_case(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        storage = MemoryStorage()
        lines = [
            "FRA Claim Form", "Claim No: TN-FRA-IFR-991", "Claimant: Ramu",
            "Holder Type: Individual", "Right Type: IFR", "District: Salem",
            "Block: Yercaud", "Village: Kottur", "Survey No: 77",
            "Claimed Area: 2 acre", "Claim Date: 12/04/2024", "Claim Status: Granted",
        ]
        content = claim_image(lines)
        try:
            with sessionmaker(bind=engine, expire_on_commit=False)() as session:
                uploader = User(external_id=f"uploader-{uuid.uuid4()}", display_name="Uploader", role="user")
                reviewer = User(external_id=f"reviewer-{uuid.uuid4()}", display_name="Reviewer", role="reviewer")
                session.add_all([uploader, reviewer]); session.flush()
                upload = ingest_archive_batch(
                    session,
                    files=[ArchiveUpload("fra-claim.png", "image/png", content)],
                    source_office="District Tribal Welfare Office", district="Salem",
                    actor_id=uploader.id, idempotency_key=str(uuid.uuid4()),
                    storage=storage, scanner=SelectiveScanner(), intake_kind="new_claim",
                )
                self.assertEqual(upload["accepted"], 1)
                record = session.get(FRAArchiveRecord, uuid.UUID(upload["files"][0]["record_id"]))
                job = session.get(ProcessingJob, uuid.UUID(upload["files"][0]["processing_job_id"]))
                self.assertEqual(storage.values[record.document.storage_key], content)
                self.assertEqual(record.review_state, "pending")
                with patch("app.services.fra_job_handlers._read_archive_document", side_effect=lambda item: storage.values[item.document.storage_key]):
                    get_job_handler("archive_extract")(session, job)
                run = record.latest_extraction
                self.assertEqual(record.review_state, "needs_review")
                self.assertEqual(record.document.ocr_status, "completed")
                self.assertEqual(record.provenance_json["document_classification"]["document_type"], "claim_form")
                self.assertIn("Claimant: Ramu", run.raw_text)
                self.assertEqual(run.standardized_json["holder_name"], "Ramu")
                self.assertEqual(run.standardized_json["right_type"], "IFR")
                self.assertEqual(run.standardized_json["claim_date"], "2024-04-12")
                self.assertAlmostEqual(run.standardized_json["claimed_area_sqm"], 8093.7128448, places=3)
                self.assertEqual(run.field_evidence_json["holder_name"]["source_page"], 1)
                self.assertTrue(run.provenance_json["ocr_source_pages"][0]["lines"])
                reviewed = dict(run.standardized_json)
                reviewed["village"] = "Kottur"  # The reviewer confirms the source image.
                review_archive_record(session, record, reviewed_fields=reviewed,
                                      reviewer_id=reviewer.id, expected_revision=record.revision)
                self.assertEqual(record.review_state, "reviewed")
                claim = promote_archive_record(session, record, actor_id=reviewer.id,
                                               expected_revision=record.revision)
                session.commit()
                self.assertEqual(record.review_state, "promoted")
                self.assertEqual(claim.claim_number, "TN-FRA-IFR-991")
                self.assertEqual(claim.status, "submitted")
                self.assertEqual(claim.document_id, record.document_id)
                self.assertEqual(case_summary(claim)["location"]["village"], "Kottur")
                self.assertIn(claim, list_cases(session, user_id=reviewer.id, privileged=True))
                self.assertEqual(case_detail(session, claim, privileged=True)["source_document"]["document_type"], "claim_form")
                self.assertEqual(session.scalars(select(FRADecision).where(FRADecision.claim_id == claim.id)).all(), [])
                self.assertEqual(session.scalars(select(FRATitle).where(FRATitle.claim_id == claim.id)).all(), [])
                field = session.scalar(select(FRAFieldReview).where(FRAFieldReview.extraction_run_id == run.id,
                                                                   FRAFieldReview.field_name == "holder_name"))
                self.assertEqual(field.final_value_json, "Ramu")
                self.assertEqual(storage.values[record.document.storage_key], content)
        finally:
            engine.dispose()
