import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import AdministrativeAlias, Parcel
from app.services.parcel_resolver import ParcelLookup, ParcelResolver


GEOMETRY = {"type": "MultiPolygon", "coordinates": [[[[79.38, 10.96], [79.381, 10.96], [79.381, 10.961], [79.38, 10.961], [79.38, 10.96]]]]}


def lookup(**changes):
    fields = dict(
        state="Tamil Nadu", district="Thanjavur", taluk="Kumbakonam",
        village="Example Village", survey_number="701", subdivision_number="4B",
        document_area_sqm=1200, ocr_confidence=0.9,
    )
    fields.update(changes)
    return ParcelLookup(**fields)


class ParcelResolverTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(Parcel(
                state="Tamil Nadu", district="Thanjavur", taluk="Kumbakonam",
                village="Example Village", survey_number="701", subdivision_number="4B",
                official_area_sqm=1180, geometry=GEOMETRY,
                source="Synthetic development data", source_version="2026-01",
            ))
            session.add(AdministrativeAlias(
                level="district", alias="Thanjavoor", normalized_alias="thanjavoor",
                canonical_name="Thanjavur",
            ))
            session.commit()

    def resolve(self, request):
        with Session(self.engine) as session:
            return ParcelResolver(session, area_tolerance_percent=10).resolve(request)

    def test_resolves_unique_exact_composite_key_and_area_difference(self):
        result = self.resolve(lookup(subdivision_number="4b"))
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.match_method, "exact_composite_key")
        self.assertEqual(result.parcel["geometry"], GEOMETRY)
        self.assertAlmostEqual(result.area_difference_percent, 1.6949152542)

    def test_resolves_verified_administrative_alias(self):
        result = self.resolve(lookup(district="Thanjavoor"))
        self.assertEqual(result.status, "matched")
        self.assertIn("verified_alias:district", result.explanations)

    def test_returns_insufficient_data_without_complete_lookup_key(self):
        result = self.resolve(lookup(village=""))
        self.assertEqual(result.status, "insufficient_data")
        self.assertEqual(result.missing_fields, ["village"])

    def test_returns_not_found_with_safe_candidates_for_spelling_error(self):
        result = self.resolve(lookup(village="Exampel Village"))
        self.assertEqual(result.status, "needs_confirmation")
        self.assertEqual(len(result.alternatives), 1)
        self.assertEqual(result.alternatives[0]["village"], "Example Village")

    def test_returns_multiple_matches_for_more_than_one_safe_fuzzy_candidate(self):
        with Session(self.engine) as session:
            session.add(Parcel(
                state="Tamil Nadu", district="Thanjavur", taluk="Kumbakonam",
                village="Example Villages", survey_number="701", subdivision_number="4B",
                official_area_sqm=1190, geometry=GEOMETRY,
                source="Synthetic development data", source_version="2026-01",
            ))
            session.commit()
        result = self.resolve(lookup(village="Exampel Village"))
        self.assertEqual(result.status, "multiple_matches")
        self.assertEqual(len(result.alternatives), 2)

    def test_requires_confirmation_for_ambiguous_ocr_character(self):
        result = self.resolve(lookup(subdivision_number="4B", ambiguous_fields=["subdivision_number"]))
        self.assertEqual(result.status, "needs_confirmation")
        self.assertIsNotNone(result.parcel)

    def test_area_mismatch_warns_but_does_not_substitute_parcel(self):
        result = self.resolve(lookup(document_area_sqm=2000))
        self.assertEqual(result.status, "matched")
        self.assertIn("Document area differs from registry area beyond 10.0%.", result.warnings)

    def test_low_overall_match_confidence_requires_confirmation(self):
        with Session(self.engine) as session:
            result = ParcelResolver(
                session, area_tolerance_percent=10, automatic_match_confidence=0.85
            ).resolve(lookup(ocr_confidence=0.1, document_area_sqm=None))
        self.assertEqual(result.status, "needs_confirmation")
        self.assertLess(result.match_confidence, 0.85)


if __name__ == "__main__":
    unittest.main()
