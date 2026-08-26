"""Constrained, versioned, and explainable advisory DSS evaluation."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import or_, select

from app.db.fra_models import DSSRecommendation, FRAClaim, SchemeRuleSet
from app.services.audit import record_audit


OPERATORS = {"all", "any", "eq", "gte", "lte", "present", "absent"}
COMPARISON_OPERATORS = {"eq", "gte", "lte"}
PRESENCE_OPERATORS = {"present", "absent"}
DISCLAIMER = (
    "This recommendation is advisory only and requires departmental review; "
    "it does not approve or sanction a benefit."
)


class InvalidRuleError(ValueError):
    pass


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
    if operator in {"gte", "lte"} and (
        not isinstance(payload["value"], (int, float))
        or isinstance(payload["value"], bool)
    ):
        raise InvalidRuleError(f"The {operator} comparison value must be numeric.")
    return condition


def _leaf_result(operator: str, payload: dict, facts: dict[str, Any]) -> ConditionResult:
    fact = payload["fact"]
    is_present = fact in facts and facts[fact] is not None
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


def _active_rules(session) -> list[SchemeRuleSet]:
    today = date.today()
    return list(
        session.scalars(
            select(SchemeRuleSet)
            .where(
                SchemeRuleSet.active.is_(True),
                or_(SchemeRuleSet.effective_from.is_(None), SchemeRuleSet.effective_from <= today),
                or_(SchemeRuleSet.effective_to.is_(None), SchemeRuleSet.effective_to >= today),
            )
            .order_by(SchemeRuleSet.scheme_code, SchemeRuleSet.version)
        )
    )


def evaluate_rules(
    session,
    *,
    claim_id,
    facts: dict[str, Any],
    actor_id,
    idempotency_key: str,
    request_id: str | None = None,
) -> list[DSSRecommendation]:
    claim = session.get(FRAClaim, claim_id)
    if claim is None:
        raise ValueError("FRA claim does not exist.")
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise ValueError("A DSS idempotency key is required.")

    recommendations: list[DSSRecommendation] = []
    for rule in _active_rules(session):
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
        missing_required = {
            name
            for name in rule.required_facts_json
            if name not in facts or facts[name] is None
        }
        if missing_required:
            result = ConditionResult(
                None,
                [f"Missing required fact: {name}." for name in sorted(missing_required)],
                missing_required,
            )
        else:
            result = evaluate_condition(rule.condition_json, facts)
        outcome = (
            "insufficient_data"
            if result.value is None
            else "recommended"
            if result.value
            else "not_recommended"
        )
        output = {
            "scheme_code": rule.scheme_code,
            "scheme_name": rule.display_name,
            "rule_version": rule.version,
            "outcome": outcome,
            "reasons": result.reasons,
            "missing_inputs": sorted(result.missing_inputs),
            "recommendation": rule.recommendation_text if outcome == "recommended" else None,
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
            input_json={"facts": dict(facts)},
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
