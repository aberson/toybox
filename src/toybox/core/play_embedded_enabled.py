"""Household-scoped ``play_embedded_enabled`` surface flag.

Gates the embedded surface: themed song/joke interjection steps mid-
activity (selected at advance-time for templates with
``recommended_themes``). Default is ``True``; migration 0015 seeds.

Mirrors :mod:`toybox.core.jokes_enabled` for shape.
"""

from __future__ import annotations

import sqlite3

from ._feature_flag import FeatureFlagSetting

PLAY_EMBEDDED_ENABLED_DEFAULT: bool = True

_SETTING = FeatureFlagSetting(
    key="play_embedded_enabled",
    default=PLAY_EMBEDDED_ENABLED_DEFAULT,
)


def get(conn: sqlite3.Connection) -> bool:
    """Return the persisted ``play_embedded_enabled`` flag (default ``True``)."""
    return _SETTING.get(conn)


def set(conn: sqlite3.Connection, value: bool) -> bool:  # noqa: A001 -- mirrors test surface
    """Persist ``value`` and return the canonical bool."""
    return _SETTING.set(conn, value)


__all__ = ["PLAY_EMBEDDED_ENABLED_DEFAULT", "get", "set"]
