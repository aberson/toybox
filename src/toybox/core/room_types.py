"""Phase X — single source of truth for the room-type vocabulary.

The canonical room-type keys (``bedroom``, ``bathroom``, …) are defined
**once** here and imported by every consumer:

* :mod:`toybox.core.listing_parser` — the room-type mentions it scans a
  pasted listing for (this step, X2 — the first consumer).
* :mod:`toybox.core.room_naming` — the per-type numbering order +
  display names (this step, X2).
* :mod:`toybox.ai.room_classifier` — the CLIP zero-shot label set (X4).
* :mod:`toybox.core.room_match` — the filename keyword map (X4).
* the ``rooms.room_type`` column validator (X4/X5).

Per ``code-quality.md`` §2 "One source of truth for data-shape
constants": duplicate definitions elsewhere are a regression. A
regression test (X4) asserts the downstream consumers reference the
same constant.

This is a **leaf module** — it imports nothing from :mod:`toybox`, so
both the pure-listing parser and the ONNX classifier can depend on it
without pulling in a heavier import graph.

The vocabulary mirrors the ``themes.py`` / ``roles.py`` display-name
idiom: a :class:`enum.StrEnum` of lowercase snake_case keys plus a
:data:`ROOM_TYPE_DISPLAY_NAMES` mapping to title-cased labels for the
parent UI. ``ROOM_TYPES`` is exported as the canonical ordered tuple of
the enum members (declaration order is the stable sort key
:mod:`toybox.core.room_naming` uses).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class RoomType(StrEnum):
    """The canonical room-type taxonomy for Phase X.

    Member names are valid Python identifiers (lowercase snake_case);
    member values are the same strings, stored verbatim in the
    ``rooms.room_type`` column and used as CLIP zero-shot label keys.

    Declaration order is load-bearing: :mod:`toybox.core.room_naming`
    numbers proposed rooms type-by-type in this order, so a parsed
    ``{"bathroom": 1, "bedroom": 2}`` always renders bedrooms before
    bathrooms regardless of dict insertion order.
    """

    bedroom = "bedroom"
    bathroom = "bathroom"
    kitchen = "kitchen"
    living_room = "living_room"
    garage = "garage"
    yard = "yard"
    playroom = "playroom"
    dining_room = "dining_room"
    office = "office"


# Canonical ordered tuple of room-type *values* (the stored strings).
# Consumers that want the stable ordering without touching the enum
# import this; ``room_naming`` uses it as the per-type numbering order.
ROOM_TYPES: Final[tuple[str, ...]] = tuple(rt.value for rt in RoomType)


# Upper bound on the count for any single room type. A real home has at
# most a handful of any one room; this cap turns a pasted ``"99999999
# beds"`` (or a malformed ``room_counts`` caller) into a bounded
# allocation instead of an OOM/hang once X5 wires pasted input to an
# endpoint. Single source of truth: both :mod:`toybox.core.listing_parser`
# (clamps extracted counts) and :mod:`toybox.core.room_naming` (clamps the
# generator defensively) import this so the bound can't drift.
MAX_ROOMS_PER_TYPE: Final[int] = 50


# Title-cased rendering for the parent UI ("Bedroom #1", a room-type
# dropdown). Multi-word keys gain a space ("living_room" -> "Living
# Room"). Every :class:`RoomType` member MUST have an entry — a
# regression test asserts the mapping is exhaustive.
ROOM_TYPE_DISPLAY_NAMES: Final[dict[str, str]] = {
    RoomType.bedroom: "Bedroom",
    RoomType.bathroom: "Bathroom",
    RoomType.kitchen: "Kitchen",
    RoomType.living_room: "Living Room",
    RoomType.garage: "Garage",
    RoomType.yard: "Yard",
    RoomType.playroom: "Playroom",
    RoomType.dining_room: "Dining Room",
    RoomType.office: "Office",
}


__all__ = [
    "MAX_ROOMS_PER_TYPE",
    "ROOM_TYPES",
    "ROOM_TYPE_DISPLAY_NAMES",
    "RoomType",
]
