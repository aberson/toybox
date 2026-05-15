"""Household-scoped ``clickable_words_enabled`` kiosk-affordance flag.

Gates the K9 word-level tap-to-read kiosk affordance: tap any word in
a step's main text or a choice label, hear that word via the browser's
``speechSynthesis`` API with the persona's voice profile. When
``False``, words render as plain text (no per-word ``<span>``s, no
visual hint). Default is ``True``; migration 0015 seeds.

Mirrors :mod:`toybox.core.jokes_enabled` for shape.
"""

from __future__ import annotations

import sqlite3

from ._feature_flag import FeatureFlagSetting

CLICKABLE_WORDS_ENABLED_DEFAULT: bool = True

_SETTING = FeatureFlagSetting(
    key="clickable_words_enabled",
    default=CLICKABLE_WORDS_ENABLED_DEFAULT,
)


def get(conn: sqlite3.Connection) -> bool:
    """Return the persisted ``clickable_words_enabled`` flag (default ``True``)."""
    return _SETTING.get(conn)


def set(conn: sqlite3.Connection, value: bool) -> bool:  # noqa: A001 -- mirrors test surface
    """Persist ``value`` and return the canonical bool."""
    return _SETTING.set(conn, value)


__all__ = ["CLICKABLE_WORDS_ENABLED_DEFAULT", "get", "set"]
