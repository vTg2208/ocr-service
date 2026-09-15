"""Constrained, versioned, and explainable advisory DSS evaluation."""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import or_, select

from app.db.fra_models import DSSRecommendation, FRAClaim, SchemeRuleSet
from app.services.audit import record_audit
from app.services.asset_contracts import ASSET_CLASSES, finite_number
from app.services.dss_facts import DSS_FACT_NAMES


OPERATORS = {"all", "any", "eq", "gte", "lte", "present", "absent"}
COMPARISON_OPERATORS = {"eq", "gte", "lte"}
PRESENCE_OPERATORS = {"present", "absent"}
DISCLAIMER = (
    "This recommendation is advisory only and requires departmental review; "
    "it does not approve or sanction a benefit."
)
OUTCOMES = {"recommended", "not_recommended", "insufficient_data"}
PRIORITIES = {"low": 0, "normal": 1, "high": 2, "urgent": 3}
ASSET_REQUIREMENT_FACTS = {
    "agricultural_land": "agricultural_observation",
    "water_body": "water_source_present",
    "homestead": "homestead_observation",
    "forest_cover": "forest_cover_present",
    "road": "road_access",
    "infrastructure": "infrastructure_services",
}


class InvalidRuleError(ValueError):
    pass


def recommendation_for_outcome(outcome: str, recommended_text: str) -> str:
    if outcome == "recommended":
        return recommended_text
    if outcome == "insufficient_data":
        return "Required information is incomplete; collect the missing inputs before human review."
    return "Rule conditions were not met; retain the case for human review if circumstances change."


@dataclass(frozen=True)
class ConditionResult:
    value: bool | None
    reasons: list[str] = field(default_factory=list)
    missing_inputs: set[str] = field(default_factory=set)


def validate_rule_definition(condition: dict) -> dict:
    if not isinstance(condition, dict) or len(condition) != 1:
        raise InvalidRuleError("Each DSS condition must contain exactly one operator.")
    operator, payload = next(iter(condition.items()))
    if operator not in OPERATORS:
        raise InvalidRuleError(f"Unsupported DSS rule operator: {operator}.")
    if operator in {"all", "any"}:
        if not isinstance(payload, list) or not payload:
            raise InvalidRuleError(f"The {operator} operator requires a non-empty condition list.")
        for child in payload:
            validate_rule_definition(child)
        return condition
    if not isinstance(payload, dict):
        raise InvalidRuleError(f"The {operator} operator requires an object payload.")
    required_keys = {"fact", "value"} if operator in COMPARISON_OPERATORS else {"fact"}
    if set(payload) != required_keys:
        raise InvalidRuleError(
            f"The {operator} operator requires exactly: {', '.join(sorted(required_keys))}."
        )
    if not isinstance(payload["fact"], str) or not payload["fact"].strip():
        raise InvalidRuleError("A DSS condition fact must be a non-empty string.")
    if operator in {"gte", "lte"} and not finite_number(payload["value"]):
        raise InvalidRuleError(f"The {operator} comparison value must be numeric.")
    return condition


def rule_fact_names(condition: dict) -> set[str]:
    validate_rule_definition(condition)
    operator, payload = next(iter(condition.items()))
    if operator in {"all", "any"}:
        return set().union(*(rule_fact_names(child) for child in payload))
    return {payload["fact"]}


def validate_rule_fact_contract(
    required_facts: list[str], condition: dict
) -> None:
    normalized = [str(name).strip() for name in required_facts]
    if any(not name for name in normalized):
        raise InvalidRuleError("Required DSS fact names must not be blank.")
    if len(set(normalized)) != len(normalized):
        raise InvalidRuleError("Required DSS fact names must be unique.")
    condition_facts = rule_fact_names(condition)
    referenced = set(normalized) | condition_facts
    unsupported = referenced - DSS_FACT_NAMES
    if unsupported:
        raise InvalidRuleError(
            f"Unsupported DSS fact name: {sorted(unsupported)[0]}."
        )
    undeclared = condition_facts - set(normalized)
    if undeclared:
        raise InvalidRuleError(
            "Every condition fact must be declared in required_facts: "
            f"{', '.join(sorted(undeclared))}."
        )


def validate_rule_configuration(
    *,
    required_facts: list[str],
    required_evidence: list[str],
    required_assets: list[str],
    exclusion_condition: dict | None,
    priority_conditions: list[dict],
    freshness_requirements: dict,
    recommendation_logic: dict,
) -> None:
    required = set(required_facts)
    evidence = [str(name).strip() for name in required_evidence]
    if len(set(evidence)) != len(evidence):
        raise InvalidRuleError("Required evidence fact names must be unique.")
    unsupported_evidence = set(evidence) - DSS_FACT_NAMES
    if unsupported_evidence:
        raise InvalidRuleError(
            f"Unsupported required evidence fact: {sorted(unsupported_evidence)[0]}."
        )
    if set(evidence) - required:
        raise InvalidRuleError(
            "Required evidence facts must also be declared in required_facts."
        )
    assets = [str(name).strip().casefold() for name in required_assets]
    if len(set(assets)) != len(assets):
        raise InvalidRuleError("Required asset classes must be unique.")
    unsupported_assets = set(assets) - ASSET_CLASSES
    if unsupported_assets:
        raise InvalidRuleError(
            f"Unsupported required asset class: {sorted(unsupported_assets)[0]}."
        )
    unavailable_assets = set(assets) - set(ASSET_REQUIREMENT_FACTS)
    if unavailable_assets:
        raise InvalidRuleError(
            "Required asset classes need a canonical DSS presence fact: "
            f"{', '.join(sorted(unavailable_assets))}."
        )
    if exclusion_condition:
        unsupported = rule_fact_names(exclusion_condition) - DSS_FACT_NAMES
        if unsupported:
            raise InvalidRuleError(
                f"Unsupported DSS fact name: {sorted(unsupported)[0]}."
            )
    seen_priorities = set()
    for item in priority_conditions:
        if not isinstance(item, dict) or set(item) - {"priority", "condition", "reason"}:
            raise InvalidRuleError(
                "Each priority condition accepts priority, condition, and optional reason."
            )
        priority = str(item.get("priority") or "").casefold()
        if priority not in PRIORITIES:
            raise InvalidRuleError("DSS priority must be low, normal, high, or urgent.")
        if priority in seen_priorities:
            raise InvalidRuleError("DSS priority conditions must use distinct priorities.")
        seen_priorities.add(priority)
        unsupported = rule_fact_names(item.get("condition")) - DSS_FACT_NAMES
        if unsupported:
            raise InvalidRuleError(
                f"Unsupported DSS fact name: {sorted(unsupported)[0]}."
            )
        if "reason" in item and not str(item["reason"]).strip():
            raise InvalidRuleError("DSS priority reason must not be blank.")
    for name, max_age in freshness_requirements.items():
        if name not in DSS_FACT_NAMES:
            raise InvalidRuleError(f"Unsupported freshness fact: {name}.")
        if name not in required:
            raise InvalidRuleError(
                "Freshness facts must also be declared in required_facts."
            )
        if isinstance(max_age, bool) or not isinstance(max_age, int) or not 1 <= max_age <= 3650:
            raise InvalidRuleError(
                f"Freshness for {name} must be an integer from 1 to 3650 days."
            )
    unsupported_logic = set(recommendation_logic) - OUTCOMES
    if unsupported_logic:
        raise InvalidRuleError(
            f"Unsupported recommendation outcome: {sorted(unsupported_logic)[0]}."
        )
    if any(not str(value).strip() for value in recommendation_logic.values()):
        raise InvalidRuleError("Recommendation logic text must not be blank.")


def _freshness_missing(requirements: dict, sources: dict) -> set[str]:
    stale = set()
    today = datetime.now(timezone.utc)
    for name, max_age in requirements.items():
        raw = (sources.get(name) or {}).get("observed_at")
        if not raw:
            stale.add(name)
            continue
        try:
            observed = datetime.fromisoformat(str(raw))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
        except ValueError:
            stale.add(name)
            continue
        if (today - observed.astimezone(timezone.utc)).days > int(max_age):
            stale.add(name)
    return stale


def _required_asset_result(required_assets: list[str], facts: dict) -> tuple[set[str], list[str]]:
    missing, unmet = set(), []
    for asset_class in required_assets:
        fact = ASSET_REQUIREMENT_FACTS[asset_class]
        value = facts.get(fact)
        if value is None:
            missing.add(fact)
        elif value is False or value == []:
            unmet.append(asset_class)
    return missing, unmet


def _priority_result(conditions: list[dict], facts: dict) -> tuple[str, list[str], set[str]]:
    matches = []
    missing = set()
    for item in conditions:
        result = evaluate_condition(item["condition"], facts)
        missing.update(result.missing_inputs)
        if result.value is True:
            matches.append((PRIORITIES[item["priority"]], item))
    if not matches:
        return "normal", [], missing
    item = max(matches, key=lambda value: value[0])[1]
    reason = str(item.get("reason") or f"{item['priority']} priority condition met.")
    return item["priority"], [reason], missing


def _leaf_result(operator: str, payload: dict, facts: dict[str, Any]) -> ConditionResult:
    fact = payload["fact"]
    is_present = fact in facts and facts[fact] is not None
    if operator in PRESENCE_OPERATORS and not is_present:
        return ConditionResult(None, [f"Missing fact: {fact}."], {fact})
    if operator == "present":
        return ConditionResult(
            is_present,
            [f"{fact} is present." if is_present else f"{fact} is absent."],
        )
    if operator == "absent":
        return ConditionResult(
            not is_present,
            [f"{fact} is absent." if not is_present else f"{fact} is present."],
        )
    if not is_present:
        return ConditionResult(None, [f"Missing fact: {fact}."], {fact})
    actual = facts[fact]
    expected = payload["value"]
    if operator in {"gte", "lte"} and not finite_number(actual):
        return ConditionResult(None, [f"Incompatible numeric input: {fact}; a finite number is required."], {fact})
    try:
        if operator == "eq":
            value = actual == expected
            relation = "equals" if value else "does not equal"
        elif operator == "gte":
            value = actual >= expected
            relation = "is at least" if value else "is below"
        else:
            value = actual <= expected
            relation = "is at most" if value else "is above"
    except TypeError:
        value = False
        relation = "cannot be compared with"
    return ConditionResult(value, [f"{fact} ({actual!r}) {relation} {expected!r}."])


def evaluate_condition(condition: dict, facts: dict[str, Any]) -> ConditionResult:
    validate_rule_definition(condition)
    operator, payload = next(iter(condition.items()))
    if operator not in {"all", "any"}:
        return _leaf_result(operator, payload, facts)

    children = [evaluate_condition(child, facts) for child in payload]
    reasons = [reason for child in children for reason in child.reasons]
    missing = set().union(*(child.missing_inputs for child in children))
    if operator == "all":
        value = False if any(child.value is False for child in children) else None if missing else True
    else:
        value = True if any(child.value is True for child in children) else None if missing else False
    return ConditionResult(value, reasons, missing)


def _active_rules(session, rule_set_ids=None) -> list[SchemeRuleSet]:
    today = date.today()
    statement = select(SchemeRuleSet).where(
        SchemeRuleSet.active.is_(True),
        or_(SchemeRuleSet.effective_from.is_(None), SchemeRuleSet.effective_from <= today),
        or_(SchemeRuleSet.effective_to.is_(None), SchemeRuleSet.effective_to >= today),
    )
    if rule_set_ids is not None:
        statement = statement.where(SchemeRuleSet.id.in_(list(rule_set_ids)))
    rules = list(session.scalars(statement.order_by(
        SchemeRuleSet.scheme_code, SchemeRuleSet.created_at.desc(), SchemeRuleSet.id.desc())))
    rules = [rule for rule in rules if _catalog_allows_execution(rule, today)]
    if rule_set_ids is not None:
        return rules
    # New evaluations use the most recently registered active definition per scheme.
    # Explicit version IDs and stored recommendations remain available for history.
    latest = {}
    for rule in rules:
        latest.setdefault(rule.scheme_code, rule)
    return list(latest.values())


def _catalog_allows_execution(rule: SchemeRuleSet, today: date) -> bool:
    """Legacy unlinked rules remain replayable; all linked rules obey catalogue governance."""
    entry = rule.catalog_entry
    if entry is None:
        return True
    return (
        entry.scheme_code == rule.scheme_code
        and entry.authoritative
        and entry.active
        and (entry.effective_from is None or entry.effective_from <= today)
        and (entry.effective_to is None or entry.effective_to >= today)
        and (entry.effective_from is None or (
            rule.effective_from is not None and rule.effective_from >= entry.effective_from
        ))
        and (entry.effective_to is None or (
            rule.effective_to is not None and rule.effective_to <= entry.effective_to
        ))
    )


def evaluate_rules(
    session,
    *,
    claim_id,
    facts: dict[str, Any],
    actor_id,
    idempotency_key: str,
    request_id: str | None = None,
    rule_set_ids=None,
    fact_snapshot_id=None,
    fact_sources: dict | None = None,
) -> list[DSSRecommendation]:
    claim = session.get(FRAClaim, claim_id)
    if claim is None:
        raise ValueError("FRA claim does not exist.")
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise ValueError("A DSS idempotency key is required.")

    recommendations: list[DSSRecommendation] = []
    for rule in _active_rules(session, rule_set_ids):
        existing = session.scalar(
            select(DSSRecommendation).where(
                DSSRecommendation.actor_id == actor_id,
                DSSRecommendation.rule_set_id == rule.id,
                DSSRecommendation.idempotency_key == normalized_key,
            )
        )
        if existing is not None:
            recommendations.append(existing)
            continue
        validate_rule_definition(rule.condition_json)
        if fact_snapshot_id is not None:
            validate_rule_fact_contract(
                list(rule.required_facts_json or []), rule.condition_json
            )
            validate_rule_configuration(
                required_facts=list(rule.required_facts_json or []),
                required_evidence=list(rule.required_evidence_json or []),
                required_assets=list(rule.required_assets_json or []),
                exclusion_condition=rule.exclusion_condition_json,
                priority_conditions=list(rule.priority_conditions_json or []),
                freshness_requirements=dict(rule.freshness_requirements_json or {}),
                recommendation_logic=dict(rule.recommendation_logic_json or {}),
            )
        missing_required = {
            name
            for name in rule.required_facts_json
            if name not in facts or facts[name] is None
        }
        missing_evidence = {
            name
            for name in (rule.required_evidence_json or [])
            if name not in (fact_sources or {})
            or (fact_sources or {}).get(name, {}).get("verification_state")
            == "unavailable"
        }
        stale_facts = _freshness_missing(
            dict(rule.freshness_requirements_json or {}),
            dict(fact_sources or {}),
        )
        missing_assets, unmet_assets = _required_asset_result(
            list(rule.required_assets_json or []), facts
        )
        missing_inputs = (
            missing_required | missing_evidence | stale_facts | missing_assets
        )
        if missing_inputs:
            reasons = [
                *[
                    f"Missing required fact: {name}."
                    for name in sorted(missing_required)
                ],
                *[
                    f"Required evidence is unavailable: {name}."
                    for name in sorted(missing_evidence)
                ],
                *[
                    f"Required evidence is stale: {name}."
                    for name in sorted(stale_facts)
                ],
                *[
                    f"Required asset observation is unavailable: {name}."
                    for name in sorted(missing_assets)
                ],
            ]
            result = ConditionResult(
                None,
                reasons,
                missing_inputs,
            )
        elif unmet_assets:
            result = ConditionResult(
                False,
                [
                    f"Required verified asset is not present: {name}."
                    for name in sorted(unmet_assets)
                ],
            )
        else:
            result = evaluate_condition(rule.condition_json, facts)
        exclusion_result = (
            evaluate_condition(rule.exclusion_condition_json, facts)
            if rule.exclusion_condition_json
            else ConditionResult(False)
        )
        if result.value is not False and exclusion_result.value is None:
            result = ConditionResult(
                None,
                result.reasons + exclusion_result.reasons,
                result.missing_inputs | exclusion_result.missing_inputs,
            )
        elif exclusion_result.value is True:
            result = ConditionResult(
                False,
                result.reasons
                + ["An exclusion condition was met."]
                + exclusion_result.reasons,
                result.missing_inputs,
            )
        outcome = (
            "insufficient_data"
            if result.value is None
            else "recommended"
            if result.value
            else "not_recommended"
        )
        priority, priority_reasons, priority_missing = _priority_result(
            list(rule.priority_conditions_json or []), facts
        )
        logic = dict(rule.recommendation_logic_json or {})
        recommendation = logic.get(outcome) or recommendation_for_outcome(
            outcome, rule.recommendation_text
        )
        output = {
            "scheme_code": rule.scheme_code,
            "scheme_name": rule.display_name,
            "rule_version": rule.version,
            "catalog_entry_id": str(rule.catalog_entry_id) if rule.catalog_entry_id else None,
            "catalog_version": rule.catalog_entry.version if rule.catalog_entry else None,
            "outcome": outcome,
            "reasons": result.reasons,
            "missing_inputs": sorted(result.missing_inputs),
            "required_evidence": list(rule.required_evidence_json or []),
            "required_assets": list(rule.required_assets_json or []),
            "unmet_assets": sorted(unmet_assets),
            "freshness_requirements": dict(
                rule.freshness_requirements_json or {}
            ),
            "priority": priority,
            "priority_reasons": priority_reasons,
            "priority_missing_inputs": sorted(priority_missing),
            "recommendation": recommendation,
            "source_reference": rule.source_reference,
            "advisory_only": True,
            "disclaimer": DISCLAIMER,
        }
        recommendation = DSSRecommendation(
            claim=claim,
            rule_set=rule,
            rule_version=rule.version,
            actor_id=actor_id,
            idempotency_key=normalized_key,
            outcome=outcome,
            input_json={
                "facts": dict(facts),
                "fact_snapshot_id": str(fact_snapshot_id) if fact_snapshot_id else None,
                "fact_sources": dict(fact_sources or {}),
            },
            output_json=output,
        )
        session.add(recommendation)
        session.flush()
        record_audit(
            session,
            actor_id=actor_id,
            action="dss_recommendation_evaluated",
            entity_type="fra_claim",
            entity_id=claim.id,
            after={
                "recommendation_id": str(recommendation.id),
                "scheme_code": rule.scheme_code,
                "rule_version": rule.version,
                "outcome": outcome,
                "advisory_only": True,
            },
            request_id=request_id,
        )
        recommendations.append(recommendation)
    return recommendations
