"""Transactional compare-and-swap guards for human review and claim mutations."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect, update
from sqlalchemy.orm.attributes import flag_modified, set_committed_value


def as_utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def next_timestamp(previous):
    """Keep tokens distinct even on clocks with limited resolution."""
    now = datetime.now(timezone.utc)
    if previous is not None:
        previous = as_utc(previous)
        if now <= previous:
            now = previous + timedelta(microseconds=1)
    return now


def conditional_update(session, record, *, expected, values, conflict):
    """Acquire the row's write lock before side effects; caller owns rollback."""
    table = inspect(record).mapper.local_table
    statement = update(table).where(table.c.id == record.id)
    for key, value in expected.items():
        statement = statement.where(table.c[key] == value)
    with session.no_autoflush:
        result = session.execute(statement.values(**values))
    if result.rowcount != 1:
        raise conflict
    for key, value in values.items():
        set_committed_value(record, key, value)


def reserve_revision(session, record, *, expected_revision, state_field, conflict, advance=True):
    conditional_update(
        session, record,
        expected={"revision": expected_revision, state_field: getattr(record, state_field)},
        values={"revision": expected_revision + int(advance)}, conflict=conflict,
    )


def guard_claim(session, claim, *, conflict, target_status=None):
    # A caller may have staged a legitimate change in this same transaction.
    # Compare its original stored status, then persist the local status under
    # the timestamp guard instead of autoflushing an unconditional stale write.
    state = inspect(claim)
    status_history = state.attrs.status.history
    stored_status = status_history.deleted[0] if status_history.deleted else claim.status
    timestamp_history = state.attrs.updated_at.history
    stored_timestamp = timestamp_history.deleted[0] if timestamp_history.deleted else claim.updated_at
    conditional_update(
        session, claim, expected={"status": stored_status, "updated_at": stored_timestamp},
        values={"status": target_status if target_status is not None else claim.status,
                "updated_at": next_timestamp(stored_timestamp)},
        conflict=conflict,
    )
    # Other staged claim fields must not replace the reserved token via onupdate.
    if session.is_modified(claim, include_collections=False):
        flag_modified(claim, "updated_at")
