"""Phase X Step X2 — pure room-name generation from parsed counts.

Takes a ``{room_type: count}`` mapping (the ``room_counts`` slice of a
:class:`toybox.core.listing_parser.ParsedListing`) and produces an
ordered list of proposed, per-type-numbered rooms — ``Bedroom #1``,
``Bedroom #2``, ``Bathroom #1`` — for the parent's review table.

Pure + offline + deterministic: no network, no DB, no randomness. The
output order is the :data:`toybox.core.room_types.ROOM_TYPES`
declaration order, and rooms are numbered ``1..n`` within each type.

**No "Playroom" auto-guess.** Per the Phase X plan §8 (resolved
decision), this step never invents a playroom from a bedroom surplus —
every bedroom is named ``Bedroom #n`` and the parent renames one to a
playroom in the editable table if they want one. "Playroom" stays a
valid :class:`toybox.core.room_types.RoomType` for manual selection.

**Unknown count keys are skipped (documented choice).** A key in
``room_counts`` that is not a canonical :data:`ROOM_TYPES` value is
dropped, not title-cased into a name. Reason: ``room_type`` is a
constrained vocabulary (one source of truth — ``room_types.py``); a
title-case fallback would mint a proposed room whose ``room_type`` is
not a valid value, which the validator/classifier downstream cannot
honour. The parser only ever emits canonical keys, so this only guards
against a malformed caller — failing closed (skip) keeps the proposed
set clean. Non-positive counts (``0`` or negative) are likewise skipped.
"""

from __future__ import annotations

from typing import TypedDict

from .room_types import MAX_ROOMS_PER_TYPE, ROOM_TYPE_DISPLAY_NAMES, ROOM_TYPES


class ProposedRoom(TypedDict):
    """One proposed room in the parent's review table.

    ``room_type`` is a canonical :data:`ROOM_TYPES` value; ``display_name``
    is the per-type-numbered label rendered from
    :data:`ROOM_TYPE_DISPLAY_NAMES` (e.g. ``"Bedroom #1"``). Both are
    operator-editable before commit (X5/X6).
    """

    room_type: str
    display_name: str


def propose_rooms(room_counts: dict[str, int]) -> list[ProposedRoom]:
    """Expand a ``{room_type: count}`` mapping into numbered rooms.

    Example::

        >>> propose_rooms({"bedroom": 3, "bathroom": 2})
        [{'room_type': 'bedroom', 'display_name': 'Bedroom #1'},
         {'room_type': 'bedroom', 'display_name': 'Bedroom #2'},
         {'room_type': 'bedroom', 'display_name': 'Bedroom #3'},
         {'room_type': 'bathroom', 'display_name': 'Bathroom #1'},
         {'room_type': 'bathroom', 'display_name': 'Bathroom #2'}]

    Ordering is the :data:`ROOM_TYPES` declaration order (stable,
    independent of ``room_counts`` insertion order); numbering is
    ``1..n`` within each type. An empty mapping yields ``[]``.

    Unknown keys (not in :data:`ROOM_TYPES`) and non-positive counts are
    skipped — see the module docstring for the rationale.

    Each count is clamped to :data:`MAX_ROOMS_PER_TYPE` so a malformed
    caller (e.g. ``{"bedroom": 10_000}``) yields a bounded list instead
    of an OOM/hang — the generator is safe regardless of caller.
    """
    proposed: list[ProposedRoom] = []
    for room_type in ROOM_TYPES:
        count = min(room_counts.get(room_type, 0), MAX_ROOMS_PER_TYPE)
        if count <= 0:
            continue
        display_base = ROOM_TYPE_DISPLAY_NAMES[room_type]
        for n in range(1, count + 1):
            proposed.append(
                ProposedRoom(
                    room_type=room_type,
                    display_name=f"{display_base} #{n}",
                )
            )
    return proposed


__all__ = [
    "ProposedRoom",
    "propose_rooms",
]
