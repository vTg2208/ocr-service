"""Project DSS results onto FRA holders, villages, and governed scheme entries."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.db.fra_models import DSSRecommendation, FRAClaim
from app.db.fra_operational_models import SchemeCatalogEntry
from app.services.fra_locations import claim_location, location_matches


ADVISORY_WARNING = (
    "Scheme convergence is an advisory screening result. It does not establish "
    "eligibility, approve an application, or sanction a benefit. The responsible "
    "department must verify current programme rules and source evidence."
)
CONVERGENCE_STATUSES = {
    "recommended": "potentially_eligible",
    "not_recommended": "not_indicated",
    "insufficient_data": "insufficient_data",
}
ASSET_FACT_LABELS = {
    "agricultural_observation": "agricultural_land",
    "water_source_present": "water_body",
    "homestead_observation": "homestead",
    "forest_cover_present": "forest_cover",
    "road_access": "road",
    "infrastructure_services": "infrastructure",
}


def _catalog_status(entry: SchemeCatalogEntry | None) -> str:
    if entry is None:
        return "catalog_entry_missing"
    if entry.active and entry.authoritative:
        return "active_authoritative"
    if entry.authoritative:
        return "authoritative_inactive"
    return "draft_inactive"


def _catalog_is_current(entry: SchemeCatalogEntry) -> bool:
    today = date.today()
    return (
        (entry.effective_from is None or entry.effective_from <= today)
        and (entry.effective_to is None or entry.effective_to >= today)
    )


def _latest_catalog_entries(session) -> dict[str, SchemeCatalogEntry]:
    entries = session.scalars(
        select(SchemeCatalogEntry).order_by(
            SchemeCatalogEntry.scheme_code,
            SchemeCatalogEntry.active.desc(),
            SchemeCatalogEntry.authoritative.desc(),
            SchemeCatalogEntry.created_at.desc(),
            SchemeCatalogEntry.id.desc(),
        )
    ).all()
    result: dict[str, SchemeCatalogEntry] = {}
    for entry in entries:
        current = result.get(entry.scheme_code)
        if current is None:
            result[entry.scheme_code] = entry
            continue
        if not _catalog_is_current(current) and _catalog_is_current(entry):
            result[entry.scheme_code] = entry
    return result


def _latest_recommendations(session, claim_ids: list) -> dict[tuple, DSSRecommendation]:
    if not claim_ids:
        return {}
    rows = session.scalars(
        select(DSSRecommendation)
        .where(DSSRecommendation.claim_id.in_(claim_ids))
        .order_by(DSSRecommendation.created_at.desc(), DSSRecommendation.id.desc())
    ).all()
    result: dict[tuple, DSSRecommendation] = {}
    for row in rows:
        result.setdefault((row.claim_id, row.rule_set.scheme_code), row)
    return result


def _prerequisites(definition: dict) -> list[dict]:
    result = []
    for value in definition.get("convergence_prerequisites") or []:
        if isinstance(value, str) and value.strip():
            result.append({"fact": value.strip(), "label": value.strip().replace("_", " ").title()})
        elif isinstance(value, dict) and str(value.get("fact") or "").strip():
            fact = str(value["fact"]).strip()
            result.append({
                "fact": fact,
                "label": str(value.get("label") or fact.replace("_", " ").title()).strip(),
            })
    return result


def _supporting_evidence(recommendation: DSSRecommendation | None, definition: dict) -> list[dict]:
    if recommendation is None:
        return []
    output = dict(recommendation.output_json or {})
    inputs = dict(recommendation.input_json or {})
    facts = dict(inputs.get("facts") or {})
    sources = dict(inputs.get("fact_sources") or {})
    requested = list(output.get("required_evidence") or definition.get("evidence_facts") or [])
    evidence = []
    for fact in requested:
        fact = str(fact)
        source = dict(sources.get(fact) or {})
        value = facts.get(fact)
        if value is None and not source:
            continue
        evidence.append({
            "fact": fact,
            "value": value,
            "source_entity_type": source.get("source_entity_type"),
            "source_entity_id": source.get("source_entity_id"),
            "source_version": source.get("source_version"),
            "observed_at": source.get("observed_at"),
            "verification_state": source.get("verification_state") or "unavailable",
        })
    return evidence


def _planner_context(recommendation: DSSRecommendation | None) -> dict:
    if recommendation is None:
        return {
            "mapped_assets": [], "deficiencies": [], "unknown_data": ["dss_evaluation_required"],
            "evidence_completeness_percent": None,
        }
    inputs = dict(recommendation.input_json or {})
    facts = dict(inputs.get("facts") or {})
    output = dict(recommendation.output_json or {})
    mapped_assets = []
    for fact, asset in ASSET_FACT_LABELS.items():
        value = facts.get(fact)
        present = bool(value) if isinstance(value, (bool, list)) else False
        if present:
            mapped_assets.append({"asset": asset, "fact": fact, "value": value})
    quality = facts.get("source_quality_flags")
    quality = quality if isinstance(quality, dict) else {}
    unknown = set(quality.get("unknown_facts") or [])
    unknown.update(output.get("missing_inputs") or [])
    unknown.update(output.get("priority_missing_inputs") or [])
    required = list(output.get("required_evidence") or [])
    sources = dict(inputs.get("fact_sources") or {})
    verified = sum(
        1 for fact in required
        if (sources.get(fact) or {}).get("verification_state") not in {None, "unavailable", "unverified"}
    )
    return {
        "mapped_assets": mapped_assets,
        "deficiencies": list(facts.get("asset_deficiency_indicators") or []),
        "unknown_data": sorted(unknown),
        "evidence_completeness_percent": (
            round(100 * verified / len(required), 1) if required else None
        ),
    }


def _scheme_codes_for_claim(
    claim: FRAClaim,
    catalogs: dict[str, SchemeCatalogEntry],
    recommendations: dict[tuple, DSSRecommendation],
) -> list[str]:
    codes = set(catalogs)
    codes.update(code for claim_id, code in recommendations if claim_id == claim.id)
    relevant = []
    for code in codes:
        catalog = catalogs.get(code)
        definition = dict(catalog.definition_json or {}) if catalog else {}
        applicable = {str(value).upper() for value in definition.get("applicable_right_types") or []}
        if not applicable or claim.right_type in applicable:
            relevant.append(code)
    return sorted(relevant)


def _item(claim: FRAClaim, catalog: SchemeCatalogEntry | None, recommendation: DSSRecommendation | None) -> dict:
    definition = dict(catalog.definition_json or {}) if catalog else {}
    output = dict(recommendation.output_json or {}) if recommendation else {}
    outcome = recommendation.outcome if recommendation else None
    convergence_status = CONVERGENCE_STATUSES.get(outcome, "not_evaluated")
    missing_facts = set(output.get("missing_inputs") or []) | set(output.get("unmet_assets") or [])
    prerequisites = _prerequisites(definition)
    missing_prerequisites = (
        prerequisites
        if recommendation is None
        else [item for item in prerequisites if item["fact"] in missing_facts]
    )
    recommendation_text = output.get("recommendation")
    location = claim_location(claim)
    rule = recommendation.rule_set if recommendation else None
    planner_context = _planner_context(recommendation)
    return {
        "claim_id": str(claim.id),
        "claim_number": claim.claim_number,
        "right_type": claim.right_type,
        "claim_status": claim.status,
        "holder": {
            "id": str(claim.rights_holder_id),
            "display_name": claim.rights_holder.display_name,
            "holder_type": claim.rights_holder.holder_type,
            "claimant_category": claim.rights_holder.claimant_category,
        },
        "village": {
            "id": str(claim.village_id) if claim.village_id else None,
            "name": location.get("village"),
            "block": location.get("block"),
            "district": location.get("district"),
            "state": location.get("state"),
        },
        "scheme_code": catalog.scheme_code if catalog else rule.scheme_code,
        "scheme_name": catalog.display_name if catalog else rule.display_name,
        "department": catalog.department if catalog else None,
        "catalog_version": catalog.version if catalog else None,
        "catalog_status": _catalog_status(catalog),
        "catalog_authoritative": bool(catalog and catalog.authoritative),
        "catalog_active": bool(catalog and catalog.active),
        "catalog_source_reference": catalog.source_reference if catalog else None,
        "target_scope": definition.get("target_scope") or "holder_and_village",
        "intervention_types": list(definition.get("intervention_types") or []),
        "recommended_interventions": list(definition.get("intervention_types") or []) if outcome == "recommended" else [],
        "recommendation_id": str(recommendation.id) if recommendation else None,
        "rule_version": recommendation.rule_version if recommendation else None,
        "rule_source_reference": rule.source_reference if rule else None,
        "outcome": outcome,
        "convergence_status": convergence_status,
        "potentially_eligible": True if outcome == "recommended" else False if outcome == "not_recommended" else None,
        "missing_prerequisites": missing_prerequisites,
        "supporting_evidence": _supporting_evidence(recommendation, definition),
        "priority": output.get("priority") or "normal" if recommendation else None,
        "priority_reasons": list(output.get("priority_reasons") or []),
        "reason_for_recommendation": recommendation_text if outcome == "recommended" else None,
        "reason_for_rejection": recommendation_text if outcome == "not_recommended" else None,
        "reasons": list(output.get("reasons") or []),
        "missing_information": (
            sorted(missing_facts | set(output.get("priority_missing_inputs") or []))
            if outcome == "insufficient_data"
            else ["dss_evaluation_required"] if recommendation is None else []
        ),
        "official_eligibility_decision": False,
        "advisory_only": True,
        "warning": ADVISORY_WARNING,
        **planner_context,
    }


def list_scheme_convergence(
    session,
    *,
    claim_id=None,
    visible_claim_ids=None,
    outcome: str | None = None,
    scheme_code: str | None = None,
    district: str | None = None,
    block: str | None = None,
    village: str | None = None,
    right_type: str | None = None,
    intervention_type: str | None = None,
) -> dict:
    statement = select(FRAClaim)
    if visible_claim_ids is not None:
        statement = statement.where(FRAClaim.id.in_(visible_claim_ids))
    if claim_id is not None:
        statement = statement.where(FRAClaim.id == claim_id)
    normalized_right = right_type.strip().upper() if right_type else None
    claims = [
        claim for claim in session.scalars(statement.order_by(FRAClaim.claim_number)).all()
        if location_matches(claim_location(claim), district=district, block=block, village=village)
        and (not normalized_right or claim.right_type == normalized_right)
    ]
    catalogs = _latest_catalog_entries(session)
    recommendations = _latest_recommendations(session, [claim.id for claim in claims])
    code_filter = scheme_code.strip().upper() if scheme_code else None
    items = []
    for claim in claims:
        for code in _scheme_codes_for_claim(claim, catalogs, recommendations):
            recommendation = recommendations.get((claim.id, code))
            governed_catalog = (
                recommendation.rule_set.catalog_entry
                if recommendation and recommendation.rule_set.catalog_entry is not None
                else catalogs.get(code)
            )
            item = _item(claim, governed_catalog, recommendation)
            if code_filter and item["scheme_code"] != code_filter:
                continue
            if intervention_type and intervention_type.strip() not in item["intervention_types"]:
                continue
            if outcome and item["outcome"] != outcome:
                continue
            items.append(item)
    summary = {status: 0 for status in (
        "potentially_eligible", "not_indicated", "insufficient_data", "not_evaluated"
    )}
    for item in items:
        summary[item["convergence_status"]] += 1
    return {"items": items, "summary": summary, "warning": ADVISORY_WARNING}


__all__ = ["ADVISORY_WARNING", "list_scheme_convergence"]
