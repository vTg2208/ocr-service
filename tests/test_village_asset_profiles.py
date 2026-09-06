from datetime import date

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.fra_completion_models import AssetFeature, FRAVillageProfile, VillageAssetProfile
from app.db.fra_models import FRAClaim, FRAGeometryVersion, FRATitle, RightsHolder
from app.db.models import AuditEvent, User
from app.services.village_asset_profiles import (
    claim_asset_context,
    refresh_village_asset_profiles,
)


BOUNDARY = {"type": "MultiPolygon", "coordinates": [[[[79, 10], [79.01, 10], [79.01, 10.01], [79, 10.01], [79, 10]]]]}
CLAIM_BOUNDARY = {"type": "MultiPolygon", "coordinates": [[[[79.002, 10.002], [79.004, 10.002], [79.004, 10.004], [79.002, 10.004], [79.002, 10.002]]]]}


def test_profiles_cover_every_fra_village_and_use_only_verified_assets_for_metrics():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        reviewer = User(external_id="profile-reviewer", role="reviewer")
        villages = [FRAVillageProfile(
            state_code="TN", state_name="Tamil Nadu", district_code="D", district_name="District",
            block_code="B", block_name="Block", village_code=f"V-{index}", village_name=f"Village {index}",
            boundary=BOUNDARY, tribal_groups_json=[], socioeconomic_json={}, provenance_json={},
            reference_version="1", synthetic=False,
        ) for index in range(2)]
        session.add_all([reviewer, *villages]); session.flush()
        session.add_all([
            AssetFeature(village_id=villages[0].id, asset_class="agricultural_land",
                polygon_geometry=BOUNDARY, observed_value_json={"present": True}, acquired_at=date(2026, 1, 2),
                confidence=.8, source_type="model", source_reference="scene-1",
                provenance_json={"model_name": "assets", "model_version": "1"}, verification_state="verified"),
            AssetFeature(village_id=villages[0].id, asset_class="water_body",
                point_geometry_json={"type": "Point", "coordinates": [79.005, 10.005]},
                observed_value_json={"present": True, "asset_subtype": "pond"}, acquired_at=date(2026, 1, 3),
                confidence=.6, source_type="model", source_reference="scene-1",
                provenance_json={"model_name": "assets", "model_version": "1"}, verification_state="verified"),
            AssetFeature(village_id=villages[0].id, asset_class="infrastructure",
                observed_value_json={"present": True, "asset_subtype": "borewell"}, acquired_at=date(2026, 1, 3),
                source_type="field", provenance_json={}, verification_state="verified"),
            AssetFeature(village_id=villages[0].id, asset_class="homestead",
                observed_value_json={"present": True}, acquired_at=date(2026, 1, 3),
                confidence=.99, source_type="model", provenance_json={}, verification_state="unverified"),
        ])
        session.flush()

        profiles = refresh_village_asset_profiles(session, actor_id=reviewer.id, request_id="profile-1")
        session.flush()

        assert len(profiles) == 2
        populated = next(item for item in profiles if item.village_id == villages[0].id)
        empty = next(item for item in profiles if item.village_id == villages[1].id)
        assert populated.metrics_json["agricultural_area_sqm"] > 1_000_000
        assert populated.metrics_json["water_body_count"] == 1
        assert populated.metrics_json["homestead_count"] == 0
        assert populated.metrics_json["infrastructure_types"] == ["borewell"]
        assert populated.metrics_json["asset_confidence_mean"] == .7
        assert populated.metrics_json["latest_imagery_date"] == "2026-01-03"
        assert populated.metrics_json["verified_satellite_asset_count"] == 2
        assert populated.metrics_json["assets_within_village_count"] == 2
        assert populated.metrics_json["mapped_asset_coverage_percent"] == 100
        assert populated.metrics_json["deficiency_basis"] == "verified_satellite_observations_only"
        assert populated.metrics_json["legal_role"] == "supporting_analytical_information"
        assert populated.pending_asset_count == 1
        assert populated.sources_json[0]["model_version"] == "1"
        assert empty.source_asset_count == 0
        assert empty.metrics_json["water_body_count"] == 0
        first_generated = populated.generated_at
        refresh_village_asset_profiles(session, actor_id=reviewer.id, request_id="profile-2")
        assert populated.generated_at == first_generated
        assert session.scalar(select(func.count()).select_from(VillageAssetProfile)) == 2
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 2
    engine.dispose()


def test_claim_context_links_village_titles_intersecting_and_nearby_satellite_assets():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        reviewer = User(external_id="context-reviewer", role="reviewer")
        village = FRAVillageProfile(
            state_code="TN", state_name="Tamil Nadu", district_code="D", district_name="District",
            block_code="B", block_name="Block", village_code="V", village_name="Village",
            boundary=BOUNDARY, tribal_groups_json=[], socioeconomic_json={},
            provenance_json={}, reference_version="1", synthetic=False,
        )
        holder = RightsHolder(display_name="Holder", holder_type="individual")
        session.add_all([reviewer, village, holder])
        session.flush()
        claim = FRAClaim(
            claim_number="TN-CONTEXT-1", right_type="IFR", status="granted",
            rights_holder=holder, village=village, submitted_by=reviewer.id,
            provenance_json={},
        )
        geometry = FRAGeometryVersion(
            claim=claim, version=1, geometry=CLAIM_BOUNDARY,
            source="reviewed", provenance_json={}, boundary_quality="reviewed",
            created_by=reviewer.id,
        )
        session.add_all([claim, geometry])
        session.flush()
        session.add(FRATitle(
            claim=claim, geometry_version=geometry, version=1,
            title_number="TITLE-1", active=True, issued_by=reviewer.id,
        ))
        session.add_all([
            AssetFeature(
                claim=claim, asset_class="agricultural_land",
                polygon_geometry=CLAIM_BOUNDARY,
                observed_value_json={"present": True}, source_type="model",
                verification_state="verified", provenance_json={},
            ),
            AssetFeature(
                village=village, asset_class="water_body",
                point_geometry_json={"type": "Point", "coordinates": [79.003, 10.003]},
                observed_value_json={"present": True}, source_type="model",
                verification_state="verified", provenance_json={},
            ),
            AssetFeature(
                village=village, asset_class="road",
                point_geometry_json={"type": "Point", "coordinates": [79.009, 10.003]},
                observed_value_json={"present": True}, source_type="model",
                verification_state="verified", provenance_json={},
            ),
        ])
        session.flush()

        refresh_village_asset_profiles(session, actor_id=reviewer.id)
        profile = session.scalar(select(VillageAssetProfile))
        context = claim_asset_context(session, claim)

        assert profile.metrics_json["assets_within_village_count"] == 3
        assert profile.metrics_json["assets_intersecting_fra_land_count"] == 2
        assert profile.metrics_json["assets_near_fra_land_count"] == 1
        assert profile.metrics_json["active_title_count"] == 1
        assert context["village"]["id"] == str(village.id)
        assert context["active_titles"][0]["title_number"] == "TITLE-1"
        assert context["intersecting_asset_count"] == 2
        assert context["nearby_asset_count"] == 1
        assert context["mapped_asset_coverage_percent"] == 100
        assert {item["relation"] for item in context["assets"]} == {
            "intersects_fra_land", "near_fra_land"
        }
        assert context["legal_role"] == "supporting_analytical_information"
        assert "legal validity" in context["warning"]
    engine.dispose()
