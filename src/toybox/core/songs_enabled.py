"""Household-scoped ``songs_enabled`` feature flag.

Content master for the songs corpus. When ``False``, no surface
(standalone / embedded / endings / spontaneity) delivers a song step.
Default is ``True``; migration 0015 seeds the row on first run.

Mirrors :mod:`toybox.core.jokes_enabled` for shape — see that module's
docstring for the rationale behind the shared
:mod:`toybox.core._feature_flag` helper.
"""

from __future__ import annotations

import sqlite3

from ._feature_flag import FeatureFlagSetting

SONGS_ENABLED_DEFAULT: bool = True

_SETTING = FeatureFlagSetting(key="songs_enabled", default=SONGS_ENABLED_DEFAULT)


def get(conn: sqlite3.Connection) -> bool:
    """Return the persisted ``songs_enabled`` flag (default ``True``)."""
    return _SETTING.get(conn)


def set(conn: sqlite3.Connection, value: bool) -> bool:  # noqa: A001 -- mirrors test surface
    """Persist ``value`` and return the canonical bool.

    Raises :class:`ValueError` when ``value`` is not a ``bool``. The
    API layer translates this into HTTP 422.
    """
    return _SETTING.set(conn, value)


__all__ = ["SONGS_ENABLED_DEFAULT", "get", "set"]
