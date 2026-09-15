"""Independent SQLite connections exercise database guards and cached collections."""

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_models import FRAClaim, FRADecision, FRAGeometryVersion, FRATitle, RightsHolder
from app.db.models import AuditEvent, User
from app.services.fra_claims import add_geometry_version
from app.services.fra_workflow import InvalidTransitionError, issue_title, transition_claim


GEOMETRY = {
    "type": "MultiPolygon",
    "coordinates": [[[[79, 10], [79.1, 10], [79.1, 10.1], [79, 10.1], [79, 10]]]],
}


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'concurrency.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        reviewer = User(external_id="reviewer", role="reviewer")
        claim = FRAClaim(claim_number="CONCURRENT", right_type="IFR", status="granted",
                         rights_holder=RightsHolder(display_name="Holder", holder_type="individual"),
                         submitter=reviewer)
        session.add(claim)
        session.flush()
        ids = claim.id, reviewer.id
        session.commit()
    yield engine, *ids
    engine.dispose()


def test_separate_connections_reject_stale_decision_without_audit(database):
    engine, claim_id, reviewer_id = database
    with Session(engine) as first, Session(engine) as second:
        current, stale = first.get(FRAClaim, claim_id), second.get(FRAClaim, claim_id)
        transition_claim(first, current, target_status="superseded", authority_level="dlc",
                         outcome="superseded", reasons=["First decision"], actor_id=reviewer_id,
                         request_id="first")
        first.commit()
        with pytest.raises(InvalidTransitionError, match="changed"):
            transition_claim(second, stale, target_status="superseded", authority_level="dlc",
                             outcome="superseded", reasons=["Stale decision"], actor_id=reviewer_id,
                             request_id="stale")
        second.rollback()
    with Session(engine) as check:
        assert check.get(FRAClaim, claim_id).status == "superseded"
        assert check.scalar(select(func.count()).select_from(FRADecision)) == 1
        assert check.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_refreshed_token_reloads_cached_title_and_geometry_collections(database):
    engine, claim_id, reviewer_id = database
    with Session(engine) as first, Session(engine) as second:
        current, stale = first.get(FRAClaim, claim_id), second.get(FRAClaim, claim_id)
        assert list(stale.titles) == []
        assert list(stale.geometry_versions) == []
        add_geometry_version(first, current, geometry=GEOMETRY, source="first", provenance={},
                             boundary_quality="unknown", actor_id=reviewer_id)
        issue_title(first, current, title_number="FIRST", geometry_version_id=None,
                    issued_by=reviewer_id, metadata={}, request_id=None)
        first.commit()
        # Deliberately refresh only the scalar token, leaving both collections cached.
        second.refresh(stale, ["updated_at"])
        add_geometry_version(second, stale, geometry=GEOMETRY, source="second", provenance={},
                             boundary_quality="unknown", actor_id=reviewer_id)
        issue_title(second, stale, title_number="SECOND", geometry_version_id=None,
                    issued_by=reviewer_id, metadata={}, request_id=None)
        second.commit()
    with Session(engine) as check:
        titles = check.scalars(select(FRATitle).order_by(FRATitle.version)).all()
        geometry = check.scalars(select(FRAGeometryVersion).order_by(FRAGeometryVersion.version)).all()
        assert [(item.version, item.active) for item in titles] == [(1, False), (2, True)]
        assert [(item.version, item.source) for item in geometry] == [(1, "first"), (2, "second")]
