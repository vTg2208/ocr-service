import io
import unittest
import uuid

from openpyxl import Workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.fra_completion_models import (
    FRAArchiveRecord,
    FRAExtractionRun,
    FRAFieldReview,
)
from app.db.models import Document, User
from app.services.fra_archive import review_archive_record
from app.services.fra_tabular_intake import (
    TabularUpload,
    TabularValidationError,
    ingest_tabular_archive,
    parse_tabular_upload,
)


class MemoryStorage:
    def __init__(self):
        self.values = {}
        self.put_calls = 0

    def put(self, content, suffix):
        self.put_calls += 1
        key = f"private/tabular-{self.put_calls}{suffix}"
        self.values[key] = content
        return key

    def delete(self, key):
        self.values.pop(key, None)


class Scanner:
    def scan(self, _content):
        return None


def workbook_bytes(rows):
    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


class FRATabularIntakeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory() as session:
            uploader = User(external_id="table-uploader", display_name="Uploader", role="user")
            reviewer = User(external_id="table-reviewer", display_name="Reviewer", role="reviewer")
            session.add_all([uploader, reviewer])
            session.commit()
            self.uploader_id = uploader.id
            self.reviewer_id = reviewer.id

    def tearDown(self):
        self.engine.dispose()

    def test_csv_aliases_are_standardized_with_source_row_and_header_evidence(self):
        content = (
            "Patta Holder,District,Taluk,Village,Claim Type,Claim Status,Survey No,Latitude,Longitude\n"
            "Ramu,Salem,Yercaud,Nagloor,IFR,Granted,18/2,11.783,78.209\n"
        ).encode("utf-8-sig")

        parsed = parse_tabular_upload(TabularUpload("legacy.csv", "text/csv", content))

        self.assertEqual(parsed.safe_filename, "legacy.csv")
        self.assertEqual(len(parsed.rows), 1)
        row = parsed.rows[0]
        self.assertEqual(row.source_row, 2)
        self.assertEqual(row.fields["holder_name"], "Ramu")
        self.assertEqual(row.fields["block"], "Yercaud")
        self.assertEqual(row.fields["right_type"], "IFR")
        self.assertEqual(row.fields["survey_number"], "18/2")
        self.assertEqual(row.evidence["holder_name"]["source_header"], "Patta Holder")
        self.assertEqual(row.evidence["holder_name"]["source_row"], 2)
        self.assertEqual(row.evidence["holder_name"]["source_value"], "Ramu")

    def test_xlsx_is_supported_and_formula_cells_are_rejected(self):
        content = workbook_bytes([
            ["Claimant Name", "District", "Block", "Village", "Right Type", "Claim Status"],
            ["Malli", "Salem", "Yercaud", "Nagloor", "CFR", "Submitted"],
        ])
        parsed = parse_tabular_upload(
            TabularUpload("legacy.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", content)
        )
        self.assertEqual(parsed.rows[0].fields["holder_name"], "Malli")

        formula = workbook_bytes([["Claimant Name", "District"], ["=1+1", "Salem"]])
        with self.assertRaisesRegex(TabularValidationError, "formula"):
            parse_tabular_upload(TabularUpload("formula.xlsx", None, formula))

    def test_ingest_stores_one_private_source_and_creates_reviewable_rows_with_provenance(self):
        content = (
            "Claim Number,Claimant Name,District,Block,Village,Right Type,Claim Status\n"
            "TN-1,Ramu,Salem,Yercaud,Nagloor,IFR,submitted\n"
            "TN-2,Malli,Salem,Yercaud,Semmanatham,CR,granted\n"
        ).encode()
        storage = MemoryStorage()
        upload = TabularUpload("legacy.csv", "text/csv", content)

        with self.factory() as session:
            first = ingest_tabular_archive(
                session,
                upload=upload,
                source_office="District Tribal Welfare Office",
                district="Salem",
                actor_id=self.uploader_id,
                idempotency_key="table-batch-1",
                storage=storage,
                scanner=Scanner(),
            )
            session.commit()
            second = ingest_tabular_archive(
                session,
                upload=upload,
                source_office="District Tribal Welfare Office",
                district="Salem",
                actor_id=self.uploader_id,
                idempotency_key="table-batch-1",
                storage=storage,
                scanner=Scanner(),
            )
            session.commit()

            self.assertEqual((first["accepted"], first["rejected"]), (2, 0))
            self.assertFalse(first["replayed"])
            self.assertTrue(second["replayed"])
            self.assertEqual(storage.put_calls, 1)
            self.assertEqual(session.scalar(select(func.count(Document.id))), 1)
            self.assertEqual(session.scalar(select(func.count(FRAArchiveRecord.id))), 2)
            self.assertEqual(session.scalar(select(func.count(FRAExtractionRun.id))), 2)
            self.assertGreater(session.scalar(select(func.count(FRAFieldReview.id))), 10)

            record = session.scalar(
                select(FRAArchiveRecord).where(FRAArchiveRecord.legacy_reference == "legacy:row:2")
            )
            self.assertEqual(record.review_state, "needs_review")
            self.assertFalse(record.synthetic)
            self.assertEqual(record.provenance_json["source_row"], 2)
            self.assertFalse(record.latest_extraction.provenance_json["synthetic"])
            self.assertEqual(record.latest_extraction.standardized_json["state"], "Tamil Nadu")
            self.assertEqual(record.latest_extraction.standardized_json["state_code"], "TN")
            holder = next(item for item in record.latest_extraction.field_reviews if item.field_name == "holder_name")
            self.assertEqual(holder.source_value_json, "Ramu")
            self.assertEqual(holder.evidence_json["source_header"], "Claimant Name")
            self.assertEqual(holder.extraction_method, "tabular_header_mapping")

    def test_review_records_correction_and_final_value_for_each_field(self):
        content = (
            "Claimant Name,District,Block,Village,Right Type,Claim Status\n"
            "Ramu,Salem,Yercaud,Nagloor,IFR,submitted\n"
        ).encode()
        with self.factory() as session:
            result = ingest_tabular_archive(
                session,
                upload=TabularUpload("legacy.csv", "text/csv", content),
                source_office="District Tribal Welfare Office",
                district="Salem",
                actor_id=self.uploader_id,
                idempotency_key="table-review-1",
                storage=MemoryStorage(),
                scanner=Scanner(),
            )
            record = session.get(
                FRAArchiveRecord, uuid.UUID(result["records"][0]["record_id"])
            )
            reviewed = dict(record.latest_extraction.standardized_json)
            reviewed["holder_name"] = "Ramasamy"
            review_archive_record(
                session,
                record,
                reviewed_fields=reviewed,
                reviewer_id=self.reviewer_id,
                expected_revision=0,
            )
            session.commit()

            holder = session.scalar(
                select(FRAFieldReview).where(
                    FRAFieldReview.extraction_run_id == record.latest_extraction.id,
                    FRAFieldReview.field_name == "holder_name",
                )
            )
            self.assertEqual(holder.extracted_value_json, "Ramu")
            self.assertEqual(holder.corrected_value_json, "Ramasamy")
            self.assertEqual(holder.final_value_json, "Ramasamy")
            self.assertEqual(holder.review_state, "approved")
            self.assertEqual(holder.reviewed_by, self.reviewer_id)


if __name__ == "__main__":
    unittest.main()
