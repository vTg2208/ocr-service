"""Validation and versioning for the non-executable scheme catalogue."""

from datetime import date
from urllib.parse import urlparse

from sqlalchemy import select

from app.db.fra_operational_models import SchemeCatalogEntry
from app.db.models import User
from app.services.audit import record_audit


class CatalogValidationError(ValueError):
    pass


def _date_value(value, name: str):
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise CatalogValidationError(f"{name} must be an ISO date.") from error


def create_catalog_entry(session, payload: dict, *, actor_id, request_id: str | None = None):
    if session.get(User, actor_id) is None:
        raise CatalogValidationError("The scheme catalogue actor does not exist.")
    required = ("scheme_code", "display_name", "version", "department", "source_reference")
    normalized = {name: str(payload.get(name) or "").strip() for name in required}
    if any(not normalized[name] for name in required):
        raise CatalogValidationError("Scheme code, name, version, department, and source are required.")
    effective_from = _date_value(payload.get("effective_from"), "effective_from")
    effective_to = _date_value(payload.get("effective_to"), "effective_to")
    if effective_from and effective_to and effective_to < effective_from:
        raise CatalogValidationError("effective_to cannot precede effective_from.")
    definition = payload.get("definition") or {}
    if not isinstance(definition, dict):
        raise CatalogValidationError("Scheme catalogue definition must be an object.")
    authoritative = bool(payload.get("authoritative", False))
    active = bool(payload.get("active", False))
    authority = str(payload.get("approving_authority") or "").strip() or None
    source = normalized["source_reference"]
    if "private" in source.casefold():
        raise CatalogValidationError("A public policy source reference is required.")
    if authoritative:
        parsed = urlparse(source)
        if parsed.scheme != "https" or not authority or effective_from is None or not definition.get("reviewed_on"):
            raise CatalogValidationError("Authoritative entries require an HTTPS source, approving authority, effective date, and reviewed_on date.")
        _date_value(definition["reviewed_on"], "reviewed_on")
    if active and not authoritative:
        raise CatalogValidationError("Only an authoritative approved catalogue version can be active.")
    if active:
        for previous in session.scalars(select(SchemeCatalogEntry).where(
            SchemeCatalogEntry.scheme_code == normalized["scheme_code"].upper(),
            SchemeCatalogEntry.active.is_(True),
        )):
            previous.active = False
    entry = SchemeCatalogEntry(
        scheme_code=normalized["scheme_code"].upper(), display_name=normalized["display_name"],
        version=normalized["version"], department=normalized["department"],
        description=str(payload.get("description") or "").strip() or None,
        effective_from=effective_from, effective_to=effective_to,
        approving_authority=authority, source_reference=source,
        definition_json=definition, authoritative=authoritative, active=active,
        created_by=actor_id,
    )
    session.add(entry); session.flush()
    record_audit(
        session, actor_id=actor_id, action="scheme_catalog_version_created",
        entity_type="scheme_catalog_entry", entity_id=entry.id,
        after={"scheme_code": entry.scheme_code, "version": entry.version, "authoritative": authoritative, "active": active},
        request_id=request_id,
    )
    return entry


__all__ = ["CatalogValidationError", "create_catalog_entry"]
