"""Parametric slot-value registry for offline templates.

The offline activity generator substitutes ``{name}`` placeholders in
template text. Two placeholders predate this module:

* ``{toy}`` — picked from the resolved toy catalog (or
  :data:`DEFAULT_TOY_NAME` when the catalog is empty).
* ``{slot}`` — caller-supplied via the propose request body.

This module adds two more shapes of placeholder:

* **Catalog-backed:** ``{room}`` is filled from the resolved room
  catalog (deterministic ``rng.choice``). Empty catalog falls back to
  :data:`DEFAULT_ROOM_NAME`.
* **Word-list:** ``{action_verb}``, ``{adjective}``, ``{prop}``,
  ``{body_part}``, ``{count}`` are filled from small hand-curated
  tuples below. The point is to multiply the surface-level variety
  of a single template by the cardinality of each list — one
  five-step template that uses two word slots becomes hundreds of
  distinct outputs across seeds.

The word lists are intentionally short and audit-friendly; expanding
them is a single-file change. They were chosen for kid-vocabulary
fit: silly action verbs, descriptive but positive adjectives,
everyday safe props, body parts kids can name on a stuffed-animal,
small counts that keep "find {count} things" achievable.

Signature contract: word-list fills do NOT contribute to a candidate's
feedback signature (see :data:`SIGNATURE_CONTRIBUTING_SLOTS`). A
``loved_it`` on the "tippy-toe explore" pick should still boost the
"stomp explore" pick — they're the same template with surface
variation. Catalog-backed slots (``{toy}``, ``{room}``) and the
caller's ``{slot}`` DO contribute, because those are semantic
choices the parent's feedback was about.

Adding a new slot:

1. Add a tuple of values (or wire a catalog accessor).
2. Add a branch in :meth:`SlotRegistry.fill`.
3. Add the name to :data:`KNOWN_SLOTS`.
4. Decide whether it should contribute to the signature; if yes, add
   to :data:`SIGNATURE_CONTRIBUTING_SLOTS`.
5. Update ``_schema.json`` if you want the lint to enforce the
   whitelist on new templates.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from .content_resolver import ResolvedRoom

# Kid-friendly silly verbs. Each is a single token or hyphenated mini
# phrase so it slots into "Have {toy} {action_verb} across the room"
# without breaking grammar.
ACTION_VERBS: Final[tuple[str, ...]] = (
    "tippy-toe",
    "wiggle",
    "stomp",
    "tip-tap",
    "leap",
    "spin",
    "twirl",
    "skip",
    "march",
    "crawl",
    "slither",
    "hop",
    "shimmy",
    "scoot",
    "waddle",
)

# Descriptive adjectives — all positive or silly. Kept short for kid
# pronunciation and to avoid words with multiple connotations.
ADJECTIVES: Final[tuple[str, ...]] = (
    "sparkly",
    "mighty",
    "mysterious",
    "giggly",
    "speedy",
    "ginormous",
    "cozy",
    "bouncy",
    "fluffy",
    "shimmery",
    "wobbly",
    "zippy",
    "cheerful",
    "bumpy",
    "swirly",
)

# Everyday safe props found in most homes. No consumables, no sharp
# objects, no electronics that could be damaged. The activity
# generator never asks the kid to put these in their mouth.
PROPS: Final[tuple[str, ...]] = (
    "blanket",
    "pillow",
    "spoon",
    "shoebox",
    "crayon",
    "sock",
    "paper bag",
    "toilet paper roll",
    "leaf",
    "cardboard tube",
    "scarf",
    "hat",
    "ribbon",
    "spatula",
)

# Body parts a kid can name and wiggle on demand (or a stuffed-animal
# can be invited to wiggle).
BODY_PARTS: Final[tuple[str, ...]] = (
    "fingers",
    "toes",
    "elbows",
    "knees",
    "shoulders",
    "ears",
    "nose",
    "tummy",
)

# Small counts as English words — used for "find {count} things" and
# similar. Capped at six so younger kids can succeed without help.
COUNTS: Final[tuple[str, ...]] = ("two", "three", "four", "five", "six")

# Surfaced when no rooms are in the catalog. Phrased so it slots into
# "in {room}" naturally even with no real room name available.
DEFAULT_ROOM_NAME: Final[str] = "the room you're in"

# Slots the registry knows how to fill. Templates may reference these
# names plus the built-in ``slot`` and ``toy``. Anything else is a
# template-authoring bug — the registry returns the literal
# placeholder back so the bad text shows up in the UI rather than
# crashing the generator.
KNOWN_SLOTS: Final[frozenset[str]] = frozenset(
    {"room", "action_verb", "adjective", "prop", "body_part", "count"},
)

# Slots whose value contributes to the activity's feedback signature.
# Caller-supplied ``{slot}`` and the toy name are semantic choices the
# parent's feedback was about; everything else (registry-backed
# ``{room}`` plus the word-list slots) is treated as surface variety
# so feedback aggregates per (template, slot, toy) regardless of the
# room or word fills. Including registry-backed slots here would
# require pre-computing their fills at candidate-signature time, which
# can't be done without consuming the shared RNG ahead of template
# selection — keep it out for v1; revisit if granular per-room
# feedback becomes valuable.
SIGNATURE_CONTRIBUTING_SLOTS: Final[frozenset[str]] = frozenset(
    {"toy", "slot"},
)


@dataclass(frozen=True, slots=True)
class SlotRegistry:
    """Per-propose registry binding catalog content to slot generators.

    A fresh instance is built per ``generate()`` call from the
    resolved room catalog. Word-list slots don't need binding — they
    read from the module-level tuples — but they go through the same
    ``fill`` interface so callers don't have to special-case.
    """

    rooms: tuple[ResolvedRoom, ...] = ()

    @classmethod
    def from_resolved(
        cls, available_rooms: Sequence[ResolvedRoom],
    ) -> SlotRegistry:
        return cls(rooms=tuple(available_rooms))

    def fill(self, slot_name: str, rng: random.Random) -> str:
        """Return one value for ``slot_name``, consuming ``rng`` if needed.

        Catalog-backed slots fall back to the documented sentinel when
        the catalog is empty (e.g. ``DEFAULT_ROOM_NAME`` for rooms).
        Unknown slots return the literal ``{slot_name}`` placeholder
        so a typo in a template surfaces visibly without breaking the
        propose call.
        """
        if slot_name == "room":
            if not self.rooms:
                return DEFAULT_ROOM_NAME
            names = sorted(r.display_name for r in self.rooms)
            return rng.choice(names)
        if slot_name == "action_verb":
            return rng.choice(ACTION_VERBS)
        if slot_name == "adjective":
            return rng.choice(ADJECTIVES)
        if slot_name == "prop":
            return rng.choice(PROPS)
        if slot_name == "body_part":
            return rng.choice(BODY_PARTS)
        if slot_name == "count":
            return rng.choice(COUNTS)
        return f"{{{slot_name}}}"


__all__ = [
    "ACTION_VERBS",
    "ADJECTIVES",
    "BODY_PARTS",
    "COUNTS",
    "DEFAULT_ROOM_NAME",
    "KNOWN_SLOTS",
    "PROPS",
    "SIGNATURE_CONTRIBUTING_SLOTS",
    "SlotRegistry",
]
