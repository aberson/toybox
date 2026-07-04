"""Household-scoped ``neural_voice_enabled`` flag.

Phase Z Z6: gates the kiosk's neural-voice clip playback (Z5). When
``True`` (the default) speech surfaces play the server-rendered Kokoro
clips and fall back to Web Speech on any clip failure; when ``False``
every speech surface routes straight to the browser's Web Speech path
with no clip attempts. Default is ``True``; migration 0031 seeds.

Mirrors :mod:`toybox.core.jokes_enabled` for shape.
"""

from __future__ import annotations

import sqlite3

from ._feature_flag import FeatureFlagSetting

NEURAL_VOICE_ENABLED_DEFAULT: bool = True

_SETTING = FeatureFlagSetting(
    key="neural_voice_enabled",
    default=NEURAL_VOICE_ENABLED_DEFAULT,
)


def get(conn: sqlite3.Connection) -> bool:
    """Return the persisted ``neural_voice_enabled`` flag (default ``True``)."""
    return _SETTING.get(conn)


def set(conn: sqlite3.Connection, value: bool) -> bool:  # noqa: A001 -- mirrors test surface
    """Persist ``value`` and return the canonical bool."""
    return _SETTING.set(conn, value)


__all__ = ["NEURAL_VOICE_ENABLED_DEFAULT", "get", "set"]
