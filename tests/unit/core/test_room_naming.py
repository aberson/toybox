"""Unit coverage for :mod:`toybox.core.room_naming` (Phase X Step X2)."""

from __future__ import annotations

from toybox.core.room_naming import ProposedRoom, propose_rooms
from toybox.core.room_types import MAX_ROOMS_PER_TYPE


def test_bedrooms_then_bathrooms_numbered() -> None:
    result = propose_rooms({"bedroom": 3, "bathroom": 2})
    assert result == [
        ProposedRoom(room_type="bedroom", display_name="Bedroom #1"),
        ProposedRoom(room_type="bedroom", display_name="Bedroom #2"),
        ProposedRoom(room_type="bedroom", display_name="Bedroom #3"),
        ProposedRoom(room_type="bathroom", display_name="Bathroom #1"),
        ProposedRoom(room_type="bathroom", display_name="Bathroom #2"),
    ]


def test_empty_counts_yields_empty_list() -> None:
    assert propose_rooms({}) == []


def test_order_is_declaration_order_not_insertion_order() -> None:
    # Insertion order puts bathroom first, but ROOM_TYPES order puts
    # bedroom first — output must follow the canonical declaration order.
    result = propose_rooms({"bathroom": 1, "bedroom": 1})
    assert [r["room_type"] for r in result] == ["bedroom", "bathroom"]


def test_multiword_type_display_name() -> None:
    result = propose_rooms({"living_room": 2})
    assert result == [
        ProposedRoom(room_type="living_room", display_name="Living Room #1"),
        ProposedRoom(room_type="living_room", display_name="Living Room #2"),
    ]


def test_unknown_key_is_skipped() -> None:
    # Documented choice: keys not in ROOM_TYPES are dropped (fail closed),
    # NOT title-cased into a name with an invalid room_type.
    result = propose_rooms({"bedroom": 1, "dungeon": 5, "wine_cellar": 2})
    assert result == [ProposedRoom(room_type="bedroom", display_name="Bedroom #1")]


def test_non_positive_counts_skipped() -> None:
    result = propose_rooms({"bedroom": 0, "bathroom": -3, "kitchen": 1})
    assert result == [ProposedRoom(room_type="kitchen", display_name="Kitchen #1")]


def test_count_clamped_to_max_rooms_per_type() -> None:
    # Defensive clamp: a malformed caller passing a huge count must yield a
    # bounded list (exactly MAX_ROOMS_PER_TYPE), fast — never an OOM/hang.
    result = propose_rooms({"bedroom": 10_000})
    assert len(result) == MAX_ROOMS_PER_TYPE
    assert all(r["room_type"] == "bedroom" for r in result)
    assert result[-1]["display_name"] == f"Bedroom #{MAX_ROOMS_PER_TYPE}"


def test_no_playroom_auto_guess_from_bedroom_surplus() -> None:
    # Plan §8: never invent a playroom; only the requested counts appear.
    result = propose_rooms({"bedroom": 4})
    assert all(r["room_type"] == "bedroom" for r in result)
    assert len(result) == 4
