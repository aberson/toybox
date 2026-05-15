"""Phase K K1 — Pydantic models + parse helpers for the new persona JSON columns.

Covers:

* :class:`RoleWeights` — accepts ``{}``, rejects unknown role keys,
  rejects out-of-range weights.
* :class:`VoiceProfile` — enforces rate/pitch bounds from plan §5.
* :class:`SpontaneityRates` — defaults to ``{0.0, 0.0}``, enforces
  per-content [0.0, 1.0] bounds, rejects extras.
* ``parse_*`` helpers — NULL / empty -> documented defaults, no
  exception on the custom-persona hydration path (acceptance #8).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from toybox.activities.roles import Role
from toybox.personas.models import (
    DEFAULT_ROLE_WEIGHTS_JSON,
    DEFAULT_SPONTANEITY_RATES_JSON,
    RoleWeights,
    SpontaneityRates,
    VoiceProfile,
    parse_role_weights,
    parse_spontaneity_rates,
    parse_voice_profile,
)

# ---------------------------------------------------------------------------
# RoleWeights
# ---------------------------------------------------------------------------


def test_role_weights_empty_is_uniform_pick() -> None:
    rw = RoleWeights(root={})
    assert rw.root == {}


def test_role_weights_accepts_valid_role_keys_with_in_range_weights() -> None:
    rw = RoleWeights(root={Role.quest_giver.value: 1.5, Role.friend.value: 0.5})
    assert rw.root[Role.quest_giver.value] == 1.5
    assert rw.root[Role.friend.value] == 0.5


def test_role_weights_rejects_unknown_role_key() -> None:
    with pytest.raises(ValidationError):
        RoleWeights(root={"not_a_real_role": 1.0})


def test_role_weights_rejects_negative_weight() -> None:
    with pytest.raises(ValidationError):
        RoleWeights(root={Role.friend.value: -0.1})


def test_role_weights_rejects_weight_above_two() -> None:
    with pytest.raises(ValidationError):
        RoleWeights(root={Role.friend.value: 2.5})


# ---------------------------------------------------------------------------
# VoiceProfile
# ---------------------------------------------------------------------------


def test_voice_profile_accepts_in_bounds_pair() -> None:
    profile = VoiceProfile(rate=1.0, pitch=1.0)
    assert profile.rate == 1.0
    assert profile.pitch == 1.0
    assert profile.voice_name is None


def test_voice_profile_accepts_optional_voice_name() -> None:
    profile = VoiceProfile(rate=0.9, pitch=0.7, voice_name="Daniel")
    assert profile.voice_name == "Daniel"


def test_voice_profile_rejects_rate_below_half() -> None:
    with pytest.raises(ValidationError):
        VoiceProfile(rate=0.4, pitch=1.0)


def test_voice_profile_rejects_rate_above_two() -> None:
    with pytest.raises(ValidationError):
        VoiceProfile(rate=2.1, pitch=1.0)


def test_voice_profile_rejects_pitch_below_zero() -> None:
    with pytest.raises(ValidationError):
        VoiceProfile(rate=1.0, pitch=-0.1)


def test_voice_profile_rejects_pitch_above_two() -> None:
    with pytest.raises(ValidationError):
        VoiceProfile(rate=1.0, pitch=2.5)


def test_voice_profile_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        VoiceProfile.model_validate({"rate": 1.0, "pitch": 1.0, "extra": "no"})


# ---------------------------------------------------------------------------
# SpontaneityRates
# ---------------------------------------------------------------------------


def test_spontaneity_rates_defaults_to_zero_zero() -> None:
    rates = SpontaneityRates()
    assert rates.jokes == 0.0
    assert rates.songs == 0.0


def test_spontaneity_rates_accepts_in_bounds_pair() -> None:
    rates = SpontaneityRates(jokes=0.25, songs=0.75)
    assert rates.jokes == 0.25
    assert rates.songs == 0.75


def test_spontaneity_rates_rejects_jokes_above_one() -> None:
    with pytest.raises(ValidationError):
        SpontaneityRates(jokes=1.1, songs=0.0)


def test_spontaneity_rates_rejects_negative_songs() -> None:
    with pytest.raises(ValidationError):
        SpontaneityRates(jokes=0.0, songs=-0.01)


def test_spontaneity_rates_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SpontaneityRates.model_validate({"jokes": 0.0, "songs": 0.0, "rant": 0.5})


# ---------------------------------------------------------------------------
# parse_* helpers — custom-persona hydration path (acceptance #8)
# ---------------------------------------------------------------------------


def test_parse_role_weights_null_returns_empty_mapping() -> None:
    rw = parse_role_weights(None)
    assert rw.root == {}


def test_parse_role_weights_empty_string_returns_empty_mapping() -> None:
    rw = parse_role_weights("")
    assert rw.root == {}


def test_parse_role_weights_default_json_round_trips() -> None:
    rw = parse_role_weights(DEFAULT_ROLE_WEIGHTS_JSON)
    assert rw.root == {}


def test_parse_role_weights_valid_payload() -> None:
    rw = parse_role_weights('{"friend": 1.5, "quest_giver": 0.7}')
    assert rw.root == {"friend": 1.5, "quest_giver": 0.7}


def test_parse_role_weights_rejects_non_object_json() -> None:
    with pytest.raises(ValueError):
        parse_role_weights("[1, 2, 3]")


def test_parse_voice_profile_null_returns_none() -> None:
    """NULL = system-default voice. Custom personas hit this path."""
    assert parse_voice_profile(None) is None


def test_parse_voice_profile_empty_string_returns_none() -> None:
    assert parse_voice_profile("") is None


def test_parse_voice_profile_valid_payload() -> None:
    profile = parse_voice_profile('{"rate": 1.0, "pitch": 1.4}')
    assert profile is not None
    assert profile.rate == 1.0
    assert profile.pitch == 1.4


def test_parse_spontaneity_rates_null_returns_zero_zero() -> None:
    """Custom-persona default: never interjects."""
    rates = parse_spontaneity_rates(None)
    assert rates.jokes == 0.0
    assert rates.songs == 0.0


def test_parse_spontaneity_rates_empty_string_returns_zero_zero() -> None:
    rates = parse_spontaneity_rates("")
    assert rates.jokes == 0.0
    assert rates.songs == 0.0


def test_parse_spontaneity_rates_default_json_round_trips() -> None:
    rates = parse_spontaneity_rates(DEFAULT_SPONTANEITY_RATES_JSON)
    assert rates.jokes == 0.0
    assert rates.songs == 0.0


def test_parse_spontaneity_rates_valid_payload() -> None:
    rates = parse_spontaneity_rates('{"jokes": 0.1, "songs": 0.05}')
    assert rates.jokes == 0.1
    assert rates.songs == 0.05


# ---------------------------------------------------------------------------
# parse_* helpers — corrupt input rejection
#
# Each parse helper must raise cleanly (ValueError, or JSONDecodeError which
# is a subclass of ValueError) when given malformed JSON or a JSON value
# that decodes to a non-object. This pins behaviour for the K8 hydration
# path: a corrupt DB row surfaces as a single clean exception at parse time,
# not an opaque AttributeError deep in the engine.
# ---------------------------------------------------------------------------


_CORRUPT_JSON_INPUTS: list[str] = [
    "not json",
    "{invalid",
    "{",
    "[]",
    "42",
    '"string"',
]


@pytest.mark.parametrize("raw", _CORRUPT_JSON_INPUTS)
def test_parse_role_weights_rejects_corrupt_input(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_role_weights(raw)


@pytest.mark.parametrize("raw", _CORRUPT_JSON_INPUTS)
def test_parse_voice_profile_rejects_corrupt_input(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_voice_profile(raw)


@pytest.mark.parametrize("raw", _CORRUPT_JSON_INPUTS)
def test_parse_spontaneity_rates_rejects_corrupt_input(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_spontaneity_rates(raw)


def test_parse_voice_profile_rejects_object_missing_required_keys() -> None:
    """``rate`` + ``pitch`` are required; missing them surfaces as
    pydantic.ValidationError (subclass of ValueError)."""
    with pytest.raises(ValidationError):
        parse_voice_profile('{"voice_name": "Daniel"}')
