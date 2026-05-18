"""Phase K Step K1 — single source of truth for the 12-theme taxonomy.

Themes tag corpus entries (jokes + songs) and template
``recommended_themes``. The embedded-surface engine (K12) picks a
corpus entry whose ``theme`` matches one of the template's
``recommended_themes``; the standalone-surface generator (K13) picks
by theme + age band + persona compatibility.

All consumers MUST import :class:`Theme` and :data:`THEME_DISPLAY_NAMES`
from this module. Duplicate definitions elsewhere are a regression —
see ``code-quality.md`` §2.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Theme(StrEnum):
    """The canonical theme taxonomy.

    Member values are the lowercase strings stored in corpus JSON
    files (``data/jokes/jokes.json``, ``data/songs/manifest.json``)
    and in template ``recommended_themes`` arrays.

    The original 12 themes are from documentation/phase-k-plan.md §5;
    ``feelings`` was added in Phase M Step M8 to back the SEL
    (social-emotional learning) templates introduced in M9-M12.
    """

    adventure = "adventure"
    magic = "magic"
    space = "space"
    animals = "animals"
    vehicles = "vehicles"
    food = "food"
    friendship = "friendship"
    pirates = "pirates"
    knights = "knights"
    weather = "weather"
    music = "music"
    silly = "silly"
    feelings = "feelings"


THEME_DISPLAY_NAMES: Final[dict[Theme, str]] = {
    Theme.adventure: "Adventure",
    Theme.magic: "Magic",
    Theme.space: "Space",
    Theme.animals: "Animals",
    Theme.vehicles: "Vehicles",
    Theme.food: "Food",
    Theme.friendship: "Friendship",
    Theme.pirates: "Pirates",
    Theme.knights: "Knights",
    Theme.weather: "Weather",
    Theme.music: "Music",
    Theme.silly: "Silly",
    Theme.feelings: "Feelings",
}


__all__ = ["THEME_DISPLAY_NAMES", "Theme"]
