from datetime import timedelta

from app.db.models import AuditEvent, utcnow


_LAST_AUDIT_TIMESTAMP = "last_audit_timestamp"


def record_audit(
    session, *, actor_id, action: str, entity_type: str, entity_id,
    before: dict | None = None, after: dict | None = None, request_id: str | None = None,
) -> AuditEvent:
    created_at = utcnow()
    previous = session.info.get(_LAST_AUDIT_TIMESTAMP)
    if previous is not None and created_at <= previous:
        created_at = previous + timedelta(microseconds=1)
    session.info[_LAST_AUDIT_TIMESTAMP] = created_at
    event = AuditEvent(
        actor_id=actor_id, action=action, entity_type=entity_type, entity_id=entity_id,
        before_json=before, after_json=after, request_id=request_id,
        # Windows clocks can return the same instant for consecutive calls.
        # Keep event times monotonic within a transaction so UUID ordering never
        # scrambles actions recorded by the same workflow.
        created_at=created_at,
    )
    session.add(event)
    return event
