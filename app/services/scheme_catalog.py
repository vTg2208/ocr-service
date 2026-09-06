"""Validation and versioning for the non-executable scheme catalogue."""

from datetime import date
from urllib.parse import urlparse

from sqlalchemy import select

from app.db.fra_operational_models import SchemeCatalogEntry
from app.db.models import User
from app.services.audit import record_audit
from app.services.dss_facts import DSS_FACT_NAMES


class CatalogValidationError(ValueError):
    pass


TARGET_SCOPES = {"holder", "village", "holder_and_village"}
RIGHT_TYPES = {"IFR", "CR", "CFR"}


def _validate_string_list(value, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise CatalogValidationError(f"{name} must be a list of non-empty strings.")
    normalized = [item.strip() for item in value]
    if len(normalized) != len(set(normalized)):
        raise CatalogValidationError(f"{name} cannot contain duplicates.")
    return normalized


def validate_convergence_definition(definition: dict) -> None:
    scope = definition.get("target_scope")
    if scope is not None and scope not in TARGET_SCOPES:
        raise CatalogValidationError("target_scope must be holder, village, or holder_and_village.")
    right_types = definition.get("applicable_right_types")
    if right_types is not None:
        normalized = {item.upper() for item in _validate_string_list(right_types, "applicable_right_types")}
        if not normalized <= RIGHT_TYPES:
            raise CatalogValidationError("applicable_right_types contains an unknown FRA right type.")
    facts = []
    prerequisites = definition.get("convergence_prerequisites")
    if prerequisites is not None:
        if not isinstance(prerequisites, list):
            raise CatalogValidationError("convergence_prerequisites must be a list.")
        for item in prerequisites:
            if isinstance(item, str):
                fact = item.strip()
            elif isinstance(item, dict):
                fact = str(item.get("fact") or "").strip()
                label = item.get("label")
                if label is not None and (not isinstance(label, str) or not label.strip()):
                    raise CatalogValidationError("A convergence prerequisite label must be non-empty.")
            else:
                fact = ""
            if not fact:
                raise CatalogValidationError("Each convergence prerequisite requires a fact.")
            facts.append(fact)
    evidence = definition.get("evidence_facts")
    if evidence is not None:
        facts.extend(_validate_string_list(evidence, "evidence_facts"))
    if len(facts) != len(set(facts)) and prerequisites is not None:
        prerequisite_facts = [
            item.strip() if isinstance(item, str) else str(item.get("fact") or "").strip()
            for item in prerequisites
        ]
        if len(prerequisite_facts) != len(set(prerequisite_facts)):
            raise CatalogValidationError("convergence_prerequisites cannot contain duplicate facts.")
    unknown = sorted(set(facts) - DSS_FACT_NAMES)
    if unknown:
        raise CatalogValidationError(f"Scheme definition references unknown DSS fact: {', '.join(unknown)}.")
    interventions = definition.get("intervention_types")
    if interventions is not None:
        _validate_string_list(interventions, "intervention_types")


def validate_rule_catalog_binding(
    session,
    *,
    catalog_entry_id,
    scheme_code: str,
    active: bool,
    effective_from: date | None,
    effective_to: date | None,
) -> SchemeCatalogEntry:
    entry = session.get(SchemeCatalogEntry, catalog_entry_id)
    if entry is None:
        raise CatalogValidationError("The linked scheme catalogue version does not exist.")
    if entry.scheme_code != scheme_code.strip().upper():
        raise CatalogValidationError("The rule scheme code must match its catalogue version.")
    if active and (not entry.active or not entry.authoritative):
        raise CatalogValidationError(
            "An executable rule requires an active, authoritative scheme catalogue version."
        )
    if active:
        starts_inside = entry.effective_from is None or (
            effective_from is not None and effective_from >= entry.effective_from
        )
        ends_inside = entry.effective_to is None or (
            effective_to is not None and effective_to <= entry.effective_to
        )
        if not starts_inside or not ends_inside:
            raise CatalogValidationError(
                "An executable rule effective period must be contained by its catalogue version."
            )
    return entry


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
    validate_convergence_definition(definition)
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


__all__ = [
    "CatalogValidationError", "create_catalog_entry", "validate_convergence_definition",
    "validate_rule_catalog_binding",
]
