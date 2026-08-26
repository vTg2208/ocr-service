import pytest

from app.services.state_profiles import UnsupportedStateError, get_state_profile


def test_tamil_nadu_profile_normalizes_administration():
    profile = get_state_profile("tn")

    assert profile.code == "TN"
    assert profile.name == "Tamil Nadu"
    assert profile.normalize_district("  Thanjavur ") == "Thanjavur"
    assert profile.normalize_block("  kumbakonam  ") == "Kumbakonam"
    assert profile.normalize_village(" north   kottur ") == "North Kottur"
    assert profile.hierarchy == ("state", "district", "block", "village")
    assert profile.languages == ("ta", "en")


@pytest.mark.parametrize("identifier", ["TN", "tn", "Tamil Nadu", " tamil   nadu "])
def test_tamil_nadu_profile_accepts_code_and_canonical_name(identifier):
    assert get_state_profile(identifier).code == "TN"


def test_unsupported_state_is_explicit():
    with pytest.raises(UnsupportedStateError) as error:
        get_state_profile("Odisha")

    assert error.value.code == "unsupported_state"
    assert error.value.state == "Odisha"
