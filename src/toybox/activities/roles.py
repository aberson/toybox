"""Phase K Step K1 — single source of truth for the 10-role taxonomy.

Defines:

* :class:`Role` — :class:`enum.StrEnum` of the ten canonical role names
  used throughout Phase K (template `required_roles` / `optional_roles`,
  persona `role_weights`, slot-fill placeholders like ``{quest_giver}``,
  parent UI cast labels). Member values are the lowercase snake_case
  strings that appear verbatim as JSON keys and slot placeholders.
* :data:`ROLE_DISPLAY_NAMES` — title-cased rendering for parent UI /
  authoring tools. ``Role.guide_mentor`` → ``"Guide / Mentor"``.
* :data:`ROLE_DEFAULTS` — per-role default spontaneity rates ``{jokes_rate,
  songs_rate}`` per documentation/phase-k-plan.md §5. The kiosk advance
  engine reads these to compute ``effective_rate = max(persona.rate,
  max(role.rate for role in cast))`` per content type.
* :data:`DEFAULT_ROLE_SPONTANEITY_RATES` — alias for :data:`ROLE_DEFAULTS`
  exposed under the plan's documented name so external consumers
  (engine, validator) can import either spelling. Same object identity
  (tests assert ``is``, not ``==``, per code-quality.md §2).

All consumers MUST import these symbols from this module. Duplicate
definitions elsewhere are a regression — see ``code-quality.md`` §2
"One source of truth for data-shape constants".
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, TypedDict

from .generic_descriptors import GENERIC_DESCRIPTORS


class Role(StrEnum):
    """The 10 canonical role names for Phase K.

    Member names are valid Python identifiers (lowercase snake_case);
    member values are the same strings, used directly as JSON keys
    (persona ``role_weights``, activity ``slot_fills_json``) and as
    ``{role_name}`` placeholders inside template step text.
    """

    friend = "friend"
    quest_giver = "quest_giver"
    guide_mentor = "guide_mentor"
    needs_saving = "needs_saving"
    boss_mini_boss = "boss_mini_boss"
    big_bad_boss = "big_bad_boss"
    frenemy = "frenemy"
    sidekick = "sidekick"
    trickster = "trickster"
    helper_townsperson = "helper_townsperson"


class SpontaneityRatePair(TypedDict):
    """Per-content-type spontaneity rate pair.

    Both rates are in the closed interval ``[0.0, 1.0]``. The advance
    engine (K15) takes the max per content type across
    ``(persona ∪ every cast role)``; this is the role-side shape of
    that computation. Persona-side mirror lives at
    :class:`toybox.personas.models.SpontaneityRates` with the same
    semantics under the documented ``{jokes, songs}`` JSON keys.
    """

    jokes_rate: float
    songs_rate: float


# Per-role spontaneity defaults from documentation/phase-k-plan.md §5
# "Default role spontaneity rates". DO NOT INVENT — values are the
# verbatim numerics from the plan table. K15's max-rate computation
# reads these.
ROLE_DEFAULTS: Final[dict[Role, SpontaneityRatePair]] = {
    Role.trickster: {"jokes_rate": 0.30, "songs_rate": 0.10},
    Role.frenemy: {"jokes_rate": 0.20, "songs_rate": 0.05},
    Role.sidekick: {"jokes_rate": 0.15, "songs_rate": 0.15},
    Role.needs_saving: {"jokes_rate": 0.10, "songs_rate": 0.20},
    Role.friend: {"jokes_rate": 0.10, "songs_rate": 0.10},
    Role.boss_mini_boss: {"jokes_rate": 0.10, "songs_rate": 0.00},
    Role.helper_townsperson: {"jokes_rate": 0.05, "songs_rate": 0.10},
    Role.quest_giver: {"jokes_rate": 0.05, "songs_rate": 0.10},
    Role.big_bad_boss: {"jokes_rate": 0.05, "songs_rate": 0.00},
    Role.guide_mentor: {"jokes_rate": 0.05, "songs_rate": 0.05},
}

# Alias under the documented plan name. ``is`` equality with
# :data:`ROLE_DEFAULTS` per code-quality.md §2.
DEFAULT_ROLE_SPONTANEITY_RATES: Final[dict[Role, SpontaneityRatePair]] = ROLE_DEFAULTS


# Title-cased rendering for parent UI ("Quest Giver: Wise Owl") and
# the activity ``cast_summary`` wire field (K7).
ROLE_DISPLAY_NAMES: Final[dict[Role, str]] = {
    Role.friend: "Friend",
    Role.quest_giver: "Quest Giver",
    Role.guide_mentor: "Guide / Mentor",
    Role.needs_saving: "Needs Saving",
    Role.boss_mini_boss: "Boss / Mini-Boss",
    Role.big_bad_boss: "Big Bad Boss",
    Role.frenemy: "Frenemy",
    Role.sidekick: "Sidekick",
    Role.trickster: "Trickster",
    Role.helper_townsperson: "Helper / Townsperson",
}


__all__ = [
    "DEFAULT_ROLE_SPONTANEITY_RATES",
    "GENERIC_DESCRIPTORS",
    "ROLE_DEFAULTS",
    "ROLE_DISPLAY_NAMES",
    "Role",
    "SpontaneityRatePair",
]
