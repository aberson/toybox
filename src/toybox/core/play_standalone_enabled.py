"""Household-scoped ``play_standalone_enabled`` surface flag.

Gates the standalone surface: ``request_song`` / ``request_joke``
trigger phrases produce single-step activities through the normal
propose flow only when this AND the relevant content-master flag are
both true. Default is ``True``; migration 0015 seeds the row.

Mirrors :mod:`toybox.core.jokes_enabled` for shape — see that module's
docstring.
"""

from __future__ import annotations

import sqlite3

from ._feature_flag import FeatureFlagSetting

PLAY_STANDALONE_ENABLED_DEFAULT: bool = True

_SETTING = FeatureFlagSetting(
    key="play_standalone_enabled",
    default=PLAY_STANDALONE_ENABLED_DEFAULT,
)


def get(conn: sqlite3.Connection) -> bool:
    """Return the persisted ``play_standalone_enabled`` flag (default ``True``)."""
    return _SETTING.get(conn)


def set(conn: sqlite3.Connection, value: bool) -> bool:  # noqa: A001 -- mirrors test surface
    """Persist ``value`` and return the canonical bool."""
    return _SETTING.set(conn, value)


__all__ = ["PLAY_STANDALONE_ENABLED_DEFAULT", "get", "set"]
