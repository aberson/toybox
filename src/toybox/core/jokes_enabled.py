"""Household-scoped ``jokes_enabled`` feature flag.

Content master for the jokes corpus. When ``False``, no surface
(standalone / embedded / endings / spontaneity) delivers a joke step.
Default is ``True``; migration 0015 seeds the row on first run.

Follows the feature-flag settings convention — defensive get with
WARNING-and-fallback, set with type validation, UPSERT semantics.
Implementation lives in :mod:`toybox.core._feature_flag`.
"""

from __future__ import annotations

import sqlite3

from ._feature_flag import FeatureFlagSetting

JOKES_ENABLED_DEFAULT: bool = True

_SETTING = FeatureFlagSetting(key="jokes_enabled", default=JOKES_ENABLED_DEFAULT)


def get(conn: sqlite3.Connection) -> bool:
    """Return the persisted ``jokes_enabled`` flag (default ``True``)."""
    return _SETTING.get(conn)


def set(conn: sqlite3.Connection, value: bool) -> bool:  # noqa: A001 -- mirrors test surface
    """Persist ``value`` and return the canonical bool.

    Raises :class:`ValueError` when ``value`` is not a ``bool``. The
    API layer translates this into HTTP 422.
    """
    return _SETTING.set(conn, value)


__all__ = ["JOKES_ENABLED_DEFAULT", "get", "set"]
