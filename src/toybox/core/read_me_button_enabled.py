"""Household-scoped ``read_me_button_enabled`` kiosk-affordance flag.

Gates the K9 "Read Me" watermarked bubble at the bottom-left of each
text-bearing step card. When ``False``, the button is not rendered —
the kiosk still supports advance + choice; word-level click-to-read
(if also enabled) still works. Default is ``True``; migration 0015
seeds.

Mirrors :mod:`toybox.core.jokes_enabled` for shape.
"""

from __future__ import annotations

import sqlite3

from ._feature_flag import FeatureFlagSetting

READ_ME_BUTTON_ENABLED_DEFAULT: bool = True

_SETTING = FeatureFlagSetting(
    key="read_me_button_enabled",
    default=READ_ME_BUTTON_ENABLED_DEFAULT,
)


def get(conn: sqlite3.Connection) -> bool:
    """Return the persisted ``read_me_button_enabled`` flag (default ``True``)."""
    return _SETTING.get(conn)


def set(conn: sqlite3.Connection, value: bool) -> bool:  # noqa: A001 -- mirrors test surface
    """Persist ``value`` and return the canonical bool."""
    return _SETTING.set(conn, value)


__all__ = ["READ_ME_BUTTON_ENABLED_DEFAULT", "get", "set"]
