from app.db.models import AuditEvent


def record_audit(
    session, *, actor_id, action: str, entity_type: str, entity_id,
    before: dict | None = None, after: dict | None = None, request_id: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_id=actor_id, action=action, entity_type=entity_type, entity_id=entity_id,
        before_json=before, after_json=after, request_id=request_id,
    )
    session.add(event)
    return event
