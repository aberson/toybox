"""Household-scoped ``play_endings_enabled`` surface flag.

Gates the endings surface: themed ending steps appended at activity
creation when a template declares ``ending_step``. Default is ``True``;
migration 0015 seeds.

Mirrors :mod:`toybox.core.jokes_enabled` for shape.
"""

from __future__ import annotations

import sqlite3

from ._feature_flag import FeatureFlagSetting

PLAY_ENDINGS_ENABLED_DEFAULT: bool = True

_SETTING = FeatureFlagSetting(
    key="play_endings_enabled",
    default=PLAY_ENDINGS_ENABLED_DEFAULT,
)


def get(conn: sqlite3.Connection) -> bool:
    """Return the persisted ``play_endings_enabled`` flag (default ``True``)."""
    return _SETTING.get(conn)


def set(conn: sqlite3.Connection, value: bool) -> bool:  # noqa: A001 -- mirrors test surface
    """Persist ``value`` and return the canonical bool."""
    return _SETTING.set(conn, value)


__all__ = ["PLAY_ENDINGS_ENABLED_DEFAULT", "get", "set"]
