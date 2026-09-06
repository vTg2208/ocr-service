import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_models import FRAClaim, RightsHolder, SchemeRuleSet
from app.db.models import User
from app.services.dss_engine import evaluate_rules
from app.services.scheme_catalog import (
    CatalogValidationError,
    create_catalog_entry,
    validate_rule_catalog_binding,
)


class SchemeRuleGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            actor = User(external_id="rule-governance-admin", role="admin")
            session.add(actor); session.commit(); self.actor_id = actor.id

    def tearDown(self):
        self.engine.dispose()

    def _catalog(self, session, *, active=True, authoritative=True):
        return create_catalog_entry(session, {
            "scheme_code": "JJM", "display_name": "Jal Jeevan Mission",
            "version": "approved-2026", "department": "Water Supply",
            "effective_from": "2026-01-01", "effective_to": "2026-12-31",
            "approving_authority": "Competent test authority" if authoritative else None,
            "source_reference": "https://example.gov.in/jjm",
            "definition": {"reviewed_on": "2026-01-01" if authoritative else None},
            "authoritative": authoritative, "active": active,
        }, actor_id=self.actor_id)

    def test_active_rule_requires_matching_active_authoritative_catalog(self):
        with Session(self.engine) as session:
            draft = self._catalog(session, active=False, authoritative=False)
            with self.assertRaisesRegex(CatalogValidationError, "active, authoritative"):
                validate_rule_catalog_binding(
                    session, catalog_entry_id=draft.id, scheme_code="JJM", active=True,
                    effective_from=date(2026, 2, 1), effective_to=date(2026, 10, 1),
                )
            validate_rule_catalog_binding(
                session, catalog_entry_id=draft.id, scheme_code="JJM", active=False,
                effective_from=None, effective_to=None,
            )

    def test_rule_code_and_effective_dates_must_match_catalog_version(self):
        with Session(self.engine) as session:
            catalog = self._catalog(session)
            with self.assertRaisesRegex(CatalogValidationError, "scheme code"):
                validate_rule_catalog_binding(
                    session, catalog_entry_id=catalog.id, scheme_code="PM-KISAN", active=True,
                    effective_from=date(2026, 2, 1), effective_to=date(2026, 10, 1),
                )
            with self.assertRaisesRegex(CatalogValidationError, "effective period"):
                validate_rule_catalog_binding(
                    session, catalog_entry_id=catalog.id, scheme_code="JJM", active=True,
                    effective_from=date(2025, 12, 1), effective_to=date(2026, 10, 1),
                )
            validate_rule_catalog_binding(
                session, catalog_entry_id=catalog.id, scheme_code="JJM", active=True,
                effective_from=date(2026, 2, 1), effective_to=date(2026, 10, 1),
            )

    def test_runtime_skips_a_rule_when_its_linked_catalog_is_not_approved(self):
        with Session(self.engine) as session:
            actor = session.get(User, self.actor_id)
            draft = self._catalog(session, active=False, authoritative=False)
            claim = FRAClaim(
                claim_number="TN-GOVERNANCE-1", right_type="IFR", status="granted",
                rights_holder=RightsHolder(display_name="Holder", holder_type="individual"),
                submitter=actor,
            )
            rule = SchemeRuleSet(
                catalog_entry_id=draft.id, scheme_code="JJM", display_name="Draft JJM rule",
                version="draft-rule", required_facts_json=["water_source_present"],
                condition_json={"eq": {"fact": "water_source_present", "value": False}},
                recommendation_text="Review", source_reference="candidate://jjm",
                active=True, creator=actor,
            )
            session.add_all([claim, rule]); session.flush()
            self.assertEqual(evaluate_rules(
                session, claim_id=claim.id, facts={"water_source_present": False},
                actor_id=actor.id, idempotency_key="draft-must-not-run",
            ), [])


if __name__ == "__main__":
    unittest.main()
