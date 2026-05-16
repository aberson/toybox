"""Topic extraction from recent transcripts.

Maps spoken text to themes from the 12-theme taxonomy in
:mod:`toybox.activities.themes`. Output drives positive bias in
:func:`toybox.activities.generator._select_template` when the parent
hits "Trigger now" or "New activity" — recent kid speech tilts the
template pool toward whichever themes the kid was just talking about.

Pure offline regex over a curated synonym map. No model dependency,
deterministic, fast (single combined pattern compiled once per import).

Synonym choices are deliberate: each word maps to exactly ONE theme so
the same utterance can't double-count. Ambiguous words are mapped to
their most-evocative theme (``"treasure"`` → pirates, ``"dragon"`` →
knights, ``"rocket"`` → space). The map is intentionally conservative
— better to miss a theme than to over-bias on a generic word.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Final

from .themes import Theme

# Per-theme synonym sets. Each word maps to exactly one theme; review
# additions against the existing entries to keep that invariant.
_THEME_KEYWORDS: Final[dict[Theme, frozenset[str]]] = {
    Theme.adventure: frozenset(
        {
            "adventure",
            "explore",
            "exploring",
            "explorer",
            "quest",
            "journey",
            "mission",
            "expedition",
            "camping",
            "camp",
            "hike",
            "hiking",
            "trail",
            "mountain",
            "jungle",
            "forest",
            "island",
            "discover",
            "brave",
            "secret",
        }
    ),
    Theme.magic: frozenset(
        {
            "magic",
            "magical",
            "wizard",
            "witch",
            "witches",
            "spell",
            "spells",
            "potion",
            "fairy",
            "fairies",
            "enchanted",
            "sorcery",
            "charm",
            "mystical",
            "unicorn",
            "unicorns",
        }
    ),
    Theme.space: frozenset(
        {
            "space",
            "planet",
            "planets",
            "rocket",
            "rockets",
            "stars",
            "moon",
            "mars",
            "alien",
            "aliens",
            "astronaut",
            "astronauts",
            "galaxy",
            "comet",
            "spaceship",
            "orbit",
        }
    ),
    Theme.animals: frozenset(
        {
            "dog",
            "dogs",
            "puppy",
            "puppies",
            "cat",
            "cats",
            "kitten",
            "kittens",
            "bear",
            "bears",
            "lion",
            "tiger",
            "fish",
            "bird",
            "birds",
            "rabbit",
            "monkey",
            "elephant",
            "zoo",
            "animal",
            "animals",
            "pet",
            "pets",
            "horse",
            "cow",
            "duck",
        }
    ),
    Theme.vehicles: frozenset(
        {
            "car",
            "cars",
            "truck",
            "trucks",
            "train",
            "trains",
            "plane",
            "planes",
            "bus",
            "motorcycle",
            "bicycle",
            "bike",
            "helicopter",
            "tractor",
        }
    ),
    Theme.food: frozenset(
        {
            "food",
            "snack",
            "snacks",
            "pizza",
            "cookie",
            "cookies",
            "cake",
            "pancake",
            "pancakes",
            "apple",
            "banana",
            "lunch",
            "dinner",
            "breakfast",
            "kitchen",
            "cook",
            "cooking",
            "bake",
            "baking",
            "soup",
        }
    ),
    Theme.friendship: frozenset(
        {
            "friend",
            "friends",
            "buddy",
            "share",
            "sharing",
            "together",
            "team",
            "kind",
            "kindness",
            "help",
            "helping",
            "hug",
            "hugs",
        }
    ),
    Theme.pirates: frozenset(
        {
            "pirate",
            "pirates",
            "ship",
            "ships",
            "sea",
            "ocean",
            "treasure",
            "captain",
            "ahoy",
            "parrot",
            "boat",
            "boats",
            "sailor",
            "kraken",
        }
    ),
    Theme.knights: frozenset(
        {
            "knight",
            "knights",
            "castle",
            "castles",
            "dragon",
            "dragons",
            "sword",
            "swords",
            "kingdom",
            "prince",
            "princess",
            "royal",
            "crown",
            "queen",
            "king",
            "shield",
        }
    ),
    Theme.weather: frozenset(
        {
            "rain",
            "rainy",
            "sun",
            "sunny",
            "snow",
            "snowy",
            "snowflake",
            "wind",
            "windy",
            "thunder",
            "lightning",
            "storm",
            "cloud",
            "clouds",
            "rainbow",
        }
    ),
    Theme.music: frozenset(
        {
            "music",
            "song",
            "songs",
            "sing",
            "singing",
            "dance",
            "dancing",
            "drum",
            "drums",
            "guitar",
            "piano",
            "ukulele",
            "tambourine",
            "kazoo",
            "melody",
            "tune",
        }
    ),
    Theme.silly: frozenset(
        {
            "silly",
            "funny",
            "joke",
            "jokes",
            "weird",
            "goofy",
            "wacky",
            "giggle",
            "giggling",
            "laugh",
            "laughing",
            "wobble",
            "wiggle",
        }
    ),
}

# Single inverted index keyword → theme. Build-time assertion below
# catches duplicates so a future PR can't silently overwrite a mapping.
_KEYWORD_TO_THEME: Final[dict[str, Theme]] = {}
for _theme, _words in _THEME_KEYWORDS.items():
    for _word in _words:
        if _word in _KEYWORD_TO_THEME:
            raise RuntimeError(
                f"topic_extract synonym {_word!r} maps to both "
                f"{_KEYWORD_TO_THEME[_word]!r} and {_theme!r}; pick one"
            )
        _KEYWORD_TO_THEME[_word] = _theme

# Single combined word-boundary regex over every keyword. Compiled once
# per import; finditer over lowercased text avoids the per-match
# case-fold overhead of re.IGNORECASE.
_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in sorted(_KEYWORD_TO_THEME)) + r")\b"
)


def extract_themes(texts: Iterable[str]) -> list[Theme]:
    """Return themes mentioned across the texts, ranked by vote count.

    Each keyword match adds one vote for its theme. Themes with at
    least one vote are returned, ordered by total vote count
    (descending); ties broken by the :class:`Theme` enum's declared
    order so callers get a stable result.

    Returns an empty list when no keywords match — caller can treat
    that as a no-bias signal and fall back to the existing template
    picker.

    Args:
        texts: iterable of transcript bodies. ``None`` / empty strings
            are skipped.

    Returns:
        Themes ranked by total mentions, most-mentioned first.
    """
    counter: Counter[Theme] = Counter()
    for text in texts:
        if not text:
            continue
        for match in _PATTERN.finditer(text.lower()):
            counter[_KEYWORD_TO_THEME[match.group(1)]] += 1
    if not counter:
        return []
    theme_order = {t: i for i, t in enumerate(Theme)}
    return sorted(counter, key=lambda t: (-counter[t], theme_order[t]))


__all__ = ["extract_themes"]
