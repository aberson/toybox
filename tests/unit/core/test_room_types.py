"""Unit coverage for :mod:`toybox.core.room_types` (Phase X Step X2).

Asserts the single-source-of-truth room-type vocabulary is internally
consistent: every enum member has a display name, ``ROOM_TYPES`` mirrors
the enum, and the expected canonical keys are present.
"""

from __future__ import annotations

from toybox.core.room_types import (
    ROOM_TYPE_DISPLAY_NAMES,
    ROOM_TYPES,
    RoomType,
)

EXPECTED_KEYS = {
    "bedroom",
    "bathroom",
    "kitchen",
    "living_room",
    "garage",
    "yard",
    "playroom",
    "dining_room",
    "office",
}


def test_room_types_match_enum_values_in_order() -> None:
    assert ROOM_TYPES == tuple(rt.value for rt in RoomType)


def test_expected_canonical_keys_present() -> None:
    assert set(ROOM_TYPES) == EXPECTED_KEYS


def test_every_room_type_has_a_display_name() -> None:
    # Exhaustiveness: every member maps, and no orphan display-name keys.
    assert set(ROOM_TYPE_DISPLAY_NAMES) == set(RoomType)
    for rt in RoomType:
        assert ROOM_TYPE_DISPLAY_NAMES[rt], f"missing display name for {rt!r}"


def test_multiword_display_names_are_spaced_title_case() -> None:
    assert ROOM_TYPE_DISPLAY_NAMES[RoomType.living_room] == "Living Room"
    assert ROOM_TYPE_DISPLAY_NAMES[RoomType.dining_room] == "Dining Room"
    assert ROOM_TYPE_DISPLAY_NAMES[RoomType.bedroom] == "Bedroom"


def test_strenum_value_equals_str() -> None:
    # StrEnum members compare equal to their string value — consumers
    # (room_naming, dict keys) rely on this.
    assert RoomType.bedroom == "bedroom"
    assert ROOM_TYPE_DISPLAY_NAMES["bedroom"] == "Bedroom"
