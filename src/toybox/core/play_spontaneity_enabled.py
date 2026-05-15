"""Household-scoped ``play_spontaneity_enabled`` surface flag.

Gates the spontaneity surface: probabilistic mid-activity interjections
driven by the persona's ``spontaneity_rates`` at advance time. **Default
is ``False``** — interjections can disrupt flow, so parents must opt in
explicitly. Migration 0015 seeds the row with ``'false'``.

This is the only one of the eight Phase K feature flags that defaults
to ``False``. Mirrors :mod:`toybox.core.jokes_enabled` for shape but
with a different :data:`PLAY_SPONTANEITY_ENABLED_DEFAULT`.
"""

from __future__ import annotations

import sqlite3

from ._feature_flag import FeatureFlagSetting

# Sole opt-in flag in the Phase K cohort — see module docstring.
PLAY_SPONTANEITY_ENABLED_DEFAULT: bool = False

_SETTING = FeatureFlagSetting(
    key="play_spontaneity_enabled",
    default=PLAY_SPONTANEITY_ENABLED_DEFAULT,
)


def get(conn: sqlite3.Connection) -> bool:
    """Return the persisted ``play_spontaneity_enabled`` flag (default ``False``)."""
    return _SETTING.get(conn)


def set(conn: sqlite3.Connection, value: bool) -> bool:  # noqa: A001 -- mirrors test surface
    """Persist ``value`` and return the canonical bool."""
    return _SETTING.set(conn, value)


__all__ = ["PLAY_SPONTANEITY_ENABLED_DEFAULT", "get", "set"]
