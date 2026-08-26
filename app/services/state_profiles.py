"""State-specific administrative conventions for FRA workflows."""

from dataclasses import dataclass


def _normalized_words(value: str) -> str:
    """Normalize spacing and casing without applying administrative aliases."""

    return " ".join(value.split()).title()


@dataclass(frozen=True)
class StateProfile:
    code: str
    name: str
    hierarchy: tuple[str, ...]
    languages: tuple[str, ...]

    def normalize_district(self, value: str) -> str:
        return _normalized_words(value)

    def normalize_block(self, value: str) -> str:
        return _normalized_words(value)

    def normalize_village(self, value: str) -> str:
        return _normalized_words(value)


@dataclass(frozen=True)
class TamilNaduProfile(StateProfile):
    code: str = "TN"
    name: str = "Tamil Nadu"
    hierarchy: tuple[str, ...] = ("state", "district", "block", "village")
    languages: tuple[str, ...] = ("ta", "en")


class UnsupportedStateError(ValueError):
    code = "unsupported_state"

    def __init__(self, state: str):
        self.state = state
        super().__init__(f"State profile is not supported: {state}")


_TAMIL_NADU = TamilNaduProfile()
_PROFILES = {
    _TAMIL_NADU.code.casefold(): _TAMIL_NADU,
    _TAMIL_NADU.name.casefold(): _TAMIL_NADU,
}


def get_state_profile(code_or_name: str) -> StateProfile:
    """Return a configured profile or fail instead of silently using Tamil Nadu."""

    key = " ".join(code_or_name.split()).casefold()
    try:
        return _PROFILES[key]
    except KeyError as error:
        raise UnsupportedStateError(code_or_name) from error
