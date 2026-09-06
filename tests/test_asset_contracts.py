import pytest

from app.services.asset_contracts import (
    ASSET_CLASSES,
    canonical_label_map,
    asset_subtype,
    canonical_asset_class,
    normalize_asset_observation,
)


def test_canonical_taxonomy_uses_the_same_seven_classes_everywhere():
    assert ASSET_CLASSES == {
        "agricultural_land", "water_body", "homestead", "forest_cover",
        "road", "infrastructure", "other_asset",
    }


@pytest.mark.parametrize(
    ("source", "canonical"),
    [
        ("agricultural_cover", "agricultural_land"),
        ("cropland", "agricultural_land"),
        ("agriculture", "agricultural_land"),
        ("pond", "water_body"),
        ("river_stream", "water_body"),
        ("open_well", "infrastructure"),
        ("school", "infrastructure"),
        ("grazing_land", "other_asset"),
    ],
)
def test_source_labels_map_to_canonical_classes_and_remain_as_subtypes(source, canonical):
    asset_class, value = normalize_asset_observation(source, {"present": True})

    assert canonical_asset_class(source) == canonical
    assert asset_class == canonical
    assert value["asset_subtype"] == source
    assert asset_subtype(asset_class, value) == source


def test_model_label_maps_are_normalized_and_unknown_classes_are_rejected():
    assert canonical_label_map({"0": "cropland", "1": "pond"}) == {
        "0": "agricultural_land", "1": "water_body",
    }
    with pytest.raises(ValueError, match="Unsupported asset class"):
        canonical_label_map({"0": "unmapped_detector_class"})
