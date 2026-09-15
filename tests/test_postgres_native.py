"""PostGIS round trips and overlapping transactions; run with test_postgres.py."""

from concurrent.futures import ThreadPoolExecutor
import os
from queue import Queue
from threading import Event
import time
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.fra_completion_models import FRAArchiveRecord, AssetFeature, ProcessingJob
from app.db.fra_models import FRAClaim, FRADecision, FRAGeometryVersion, FRATitle, GramSabha, RightsHolder
from app.db.fra_operational_models import ImageryArtifact
from app.db.models import AuditEvent, Document, Parcel, User
from app.services.fra_archive import (
    ArchiveConflictError, create_archive_record, create_import_batch,
    process_archive_extraction, promote_archive_record, review_archive_record,
)
from app.services.fra_assets import AssetReviewConflict, review_asset
from app.services.fra_claims import FRAClaimConflictError, add_geometry_version
from app.services.fra_workflow import InvalidTransitionError, TitleIssuanceError, issue_title, transition_claim
from app.services.historical_evidence import HistoricalReviewConflict, review_historical_artifact
from app.services.model_gateway import ManifestFRAEntityExtractor
from app.services.processing_jobs import claim_next_job, enqueue_job


pytestmark = pytest.mark.skipif(not os.environ.get("FRA_TEST_POSTGRES_URL"),
                                reason="Run scripts/test_postgres.py for native PostGIS tests")
GEOMETRY = {"type": "MultiPolygon", "coordinates": [
    [[[79, 10], [79.01, 10], [79.01, 10.01], [79, 10.01], [79, 10]]]]}


@pytest.fixture
def claim_record(postgres_engine):
    with Session(postgres_engine) as session:
        actor = User(external_id="pg-reviewer", role="reviewer")
        claim = FRAClaim(claim_number="PG-1", right_type="IFR", status="granted",
                         rights_holder=RightsHolder(display_name="Holder", holder_type="individual"),
                         submitter=actor)
        session.add(claim)
        session.flush()
        result = claim.id, actor.id
        session.commit()
        return result


def overlap(engine, model, identifier, operation, conflict, *, commit_first=True):
    """Prove the second write is blocked by the first transaction before release."""
    loaded = Queue()
    start = Event()

    def contender():
        with Session(engine) as session:
            record = session.get(model, identifier)
            loaded.put(session.scalar(text("SELECT pg_backend_pid()")))
            if not start.wait(10):
                raise AssertionError("First transaction never released the contender")
            try:
                operation(session, record, "second")
                session.commit()
                return "committed"
            except conflict:
                session.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(contender)
        try:
            second_pid = loaded.get(timeout=10)
            with Session(engine) as first:
                record = first.get(model, identifier)
                first_pid = first.scalar(text("SELECT pg_backend_pid()"))
                operation(first, record, "first")
                start.set()
                deadline = time.monotonic() + 5
                with engine.connect() as observer:
                    while True:
                        blockers = observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid})
                        if first_pid in blockers:
                            break
                        if future.done():
                            raise AssertionError(f"Competing write did not wait: {future.result()}")
                        if time.monotonic() >= deadline:
                            raise AssertionError("PostgreSQL did not report the expected row lock")
                        time.sleep(.02)
                if commit_first:
                    first.commit()
                else:
                    first.rollback()
            assert future.result(timeout=10) == ("conflict" if commit_first else "committed")
        finally:
            start.set()


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_postgis_geometry_round_trip_srid_validity_and_intersection(postgres_engine, claim_record):
    claim_id, actor_id = claim_record
    with Session(postgres_engine) as session:
        parcel = Parcel(state="TN", district="Salem", taluk="Yercaud", village="Test",
                        survey_number="1", source="survey", geometry=GEOMETRY)
        session.add(parcel)
        version = add_geometry_version(session, session.get(FRAClaim, claim_id), geometry=GEOMETRY,
            source="survey", provenance={}, boundary_quality="surveyed", actor_id=actor_id)
        session.commit()
        version_id, parcel_id = version.id, parcel.id
    with Session(postgres_engine) as session:
        assert session.get(Parcel, parcel_id).geometry == GEOMETRY
        assert session.get(FRAGeometryVersion, version_id).geometry == GEOMETRY
        result = session.execute(text("""
            SELECT ST_SRID(g.geometry), ST_IsValid(g.geometry),
                   ST_Equals(g.geometry, p.geometry), ST_Intersects(g.geometry, p.geometry),
                   ST_Area(g.geometry::geography) > 0
            FROM fra_geometry_versions g JOIN parcels p ON p.id = :parcel
            WHERE g.id = :geometry
        """), {"parcel": parcel_id, "geometry": version_id}).one()
        assert tuple(result) == (4326, True, True, True, True)


def test_new_fra_geometry_columns_use_native_types_and_gist_indexes(postgres_engine):
    with postgres_engine.connect() as connection:
        point = connection.execute(text("""
            SELECT type, srid FROM geometry_columns
            WHERE f_table_name = 'asset_features' AND f_geometry_column = 'point_geometry_json'
        """)).one()
        indexes = connection.scalars(text("""
            SELECT indexname FROM pg_indexes
            WHERE schemaname = current_schema() AND indexdef ILIKE '% USING gist %'
        """)).all()
    assert tuple(point) == ("POINT", 4326)
    assert {
        "ix_fra_geometry_versions_geometry_gist",
        "ix_fra_village_profiles_boundary_gist",
        "ix_spatial_reference_features_geometry_gist",
        "ix_imagery_scenes_footprint_gist",
        "ix_asset_features_polygon_geometry_gist",
        "ix_asset_features_point_geometry_gist",
    } <= set(indexes)


def test_processing_workers_skip_locked_jobs_and_claim_distinct_work(postgres_engine):
    with Session(postgres_engine) as setup:
        actor = User(external_id="pg-job-owner", role="user")
        setup.add(actor)
        setup.flush()
        for sequence in (1, 2):
            enqueue_job(
                setup,
                task_type="archive_extract",
                entity_type="archive_record",
                entity_id=uuid.uuid4(),
                actor_id=actor.id,
                idempotency_key=f"pg-worker-{sequence}",
                payload={},
            )
        setup.commit()

    with Session(postgres_engine) as first, Session(postgres_engine) as second:
        first_job = claim_next_job(first, worker_id="pg-worker-a")
        second_job = claim_next_job(second, worker_id="pg-worker-b")

        assert first_job is not None
        assert second_job is not None
        assert first_job.id != second_job.id
        assert first_job.lease_token and second_job.lease_token
        first.rollback()
        second.rollback()


@pytest.mark.parametrize("kind", ["decision", "geometry", "title"])
@pytest.mark.parametrize("commit_first", [True, False], ids=["winner-commits", "winner-rolls-back"])
def test_claim_writes_wait_and_recheck_token(postgres_engine, claim_record, kind, commit_first):
    claim_id, actor_id = claim_record

    def operation(session, claim, label):
        if kind == "decision":
            return transition_claim(session, claim, target_status="superseded", authority_level="dlc",
                outcome="superseded", reasons=[label], actor_id=actor_id, request_id=label)
        if kind == "geometry":
            return add_geometry_version(session, claim, geometry=GEOMETRY, source=label, provenance={},
                boundary_quality="surveyed", actor_id=actor_id, request_id=label)
        return issue_title(session, claim, title_number=label, geometry_version_id=None,
            issued_by=actor_id, metadata={}, request_id=label)

    model, conflict = {"decision": (FRADecision, InvalidTransitionError),
                       "geometry": (FRAGeometryVersion, FRAClaimConflictError),
                       "title": (FRATitle, TitleIssuanceError)}[kind]
    overlap(postgres_engine, FRAClaim, claim_id, operation, conflict, commit_first=commit_first)
    with Session(postgres_engine) as check:
        assert count(check, model) == 1
        audits = check.scalars(select(AuditEvent)).all()
        assert [event.request_id for event in audits] == ["first" if commit_first else "second"]


@pytest.mark.parametrize("commit_first", [True, False], ids=["winner-commits", "winner-rolls-back"])
def test_archive_promotion_serializes_before_creating_holder_and_claim(postgres_engine, commit_first):
    with Session(postgres_engine) as session:
        actor = User(external_id="pg-archive-reviewer", role="reviewer")
        session.add(actor)
        session.flush()
        document = Document(uploaded_by=actor.id, storage_key="test/archive.txt", original_filename="test.txt",
            content_type="text/plain", sha256="a" * 64, idempotency_key="archive")
        session.add(document)
        session.flush()
        batch = create_import_batch(session, source_label="Test register", state="TN", actor_id=actor.id,
                                    idempotency_key="batch", synthetic=True,
                                    provenance={"source": "test register", "synthetic": True})
        record = create_archive_record(session, batch=batch, document_id=document.id,
                                       legacy_reference="PG-ARCHIVE", actor_id=actor.id)
        run = process_archive_extraction(session, record, extractor=ManifestFRAEntityExtractor("test"),
            manifest={"holder_name": "Holder", "district": "Salem", "block": "Yercaud", "village": "Test",
                      "right_type": "IFR", "claim_status": "submitted", "claim_number": "PG-ARCHIVE"},
            raw_text="Synthetic record", actor_id=actor.id)
        review_archive_record(session, record, reviewed_fields=run.standardized_json,
                              reviewer_id=actor.id, expected_revision=0)
        identifier, actor_id, revision = record.id, actor.id, record.revision
        session.commit()

    def operation(session, record, label):
        return promote_archive_record(session, record, actor_id=actor_id,
                                      expected_revision=revision, request_id=label)

    overlap(postgres_engine, FRAArchiveRecord, identifier, operation, ArchiveConflictError,
            commit_first=commit_first)
    with Session(postgres_engine) as check:
        assert count(check, FRAClaim) == count(check, RightsHolder) == count(check, GramSabha) == 1
        record = check.get(FRAArchiveRecord, identifier)
        assert (record.revision, record.review_state) == (revision + 1, "promoted")
        audits = check.scalars(select(AuditEvent).where(AuditEvent.action == "fra_archive_record_promoted")).all()
        assert [event.request_id for event in audits] == ["first" if commit_first else "second"]


def test_asset_correction_creates_one_successor_under_contention(postgres_engine, claim_record):
    claim_id, actor_id = claim_record
    with Session(postgres_engine) as session:
        asset = AssetFeature(claim_id=claim_id, asset_class="agricultural_land", source_type="field",
                             observed_value_json={"value": .6}, polygon_geometry=GEOMETRY)
        session.add(asset)
        session.flush()
        asset_id = asset.id
        session.commit()

    def operation(session, asset, label):
        return review_asset(session, asset, outcome="corrected", reviewer_id=actor_id,
            expected_revision=0, corrected_value={"value": .4}, reasons=[label], request_id=label)

    overlap(postgres_engine, AssetFeature, asset_id, operation, AssetReviewConflict)
    with Session(postgres_engine) as check:
        assert count(check, AssetFeature) == 2
        assert check.get(AssetFeature, asset_id).observed_value_json == {"value": .6}
        assert count(check, AuditEvent) == 1


@pytest.mark.parametrize("previously_reviewed", [False, True], ids=["null-token", "timestamp-token"])
def test_historical_review_serializes_nullable_timestamp(postgres_engine, claim_record, previously_reviewed):
    claim_id, actor_id = claim_record
    with Session(postgres_engine) as session:
        geometry = FRAGeometryVersion(claim_id=claim_id, version=1, geometry=GEOMETRY,
                                      source="survey", created_by=actor_id)
        session.add(geometry)
        session.flush()
        artifact = ImageryArtifact(claim_id=claim_id, geometry_version_id=geometry.id,
            artifact_type="historical_observation", target_year=2005,
            storage_key="test/history.json", content_sha256="b" * 64, processor_version="test", state="completed")
        session.add(artifact)
        session.flush()
        if previously_reviewed:
            review_historical_artifact(session, artifact, verification_state="needs_field_verification",
                notes="Initial review", expected_reviewed_at=None, reviewer_id=actor_id)
        identifier, token = artifact.id, artifact.reviewed_at
        session.commit()

    def operation(session, artifact, label):
        return review_historical_artifact(session, artifact, verification_state="verified", notes=label,
            expected_reviewed_at=token, reviewer_id=actor_id, request_id=label)

    overlap(postgres_engine, ImageryArtifact, identifier, operation, HistoricalReviewConflict)
    with Session(postgres_engine) as check:
        artifact = check.get(ImageryArtifact, identifier)
        assert artifact.provenance_json["reviewer_notes"] == "first"
        assert artifact.reviewed_at.tzinfo is not None
        assert count(check, AuditEvent) == 1 + int(previously_reviewed)
