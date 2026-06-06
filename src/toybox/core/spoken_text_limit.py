"""Household-scoped spoken text character limit setting.

Companion to the Phase R Read Me button. Stores
``settings.spoken_text_limit`` (TEXT, one of ``{0, 50, 100, 150, 250}``)
and defaults to ``150`` when the row is missing, the value is
unparseable, or the parsed integer is outside the canonical set.
Migration 0022 seeds the row on first run; legacy DBs that predate the
seed still resolve cleanly via the fallback.

``0`` means "off" — no truncation applied. Any other value is the
maximum character count at which the spoken text is truncated to a
word boundary.
"""

from __future__ import annotations

import logging
import sqlite3

_logger = logging.getLogger(__name__)


SPOKEN_TEXT_LIMIT_VALID: frozenset[int] = frozenset({0, 50, 100, 150, 250})
DEFAULT: int = 150

_SETTINGS_KEY = "spoken_text_limit"


def get_spoken_text_limit(conn: sqlite3.Connection) -> int:
    """Return the persisted spoken text limit, defaulting to 150.

    Falls back to :data:`DEFAULT` in three cases:

    1. The settings row is absent (legacy DBs that predate migration
       0022, or a deleted seed row).
    2. The value cannot be parsed as ``int`` (corrupt blob, hand-edit).
    3. The parsed integer is not in :data:`SPOKEN_TEXT_LIMIT_VALID`
       (preset list shrunk, or a free-form value snuck in).

    Cases 2 and 3 log at WARNING with the offending value truncated to
    64 chars (mirrors :mod:`toybox.core.transcript_retention`).
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (_SETTINGS_KEY,),
    ).fetchone()
    if row is None:
        return DEFAULT
    raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        truncated = raw if isinstance(raw, str) and len(raw) <= 64 else f"{str(raw)[:64]}..."
        _logger.warning(
            "settings.%s=%r unparseable as int; falling back to %d",
            _SETTINGS_KEY,
            truncated,
            DEFAULT,
        )
        return DEFAULT
    if parsed not in SPOKEN_TEXT_LIMIT_VALID:
        truncated = raw if isinstance(raw, str) and len(raw) <= 64 else f"{str(raw)[:64]}..."
        _logger.warning(
            "settings.%s=%r outside canonical set; falling back to %d",
            _SETTINGS_KEY,
            truncated,
            DEFAULT,
        )
        return DEFAULT
    return parsed


def set_spoken_text_limit(conn: sqlite3.Connection, value: int) -> int:
    """Persist ``value`` and return the canonical int.

    Raises :class:`ValueError` when ``value`` is not in
    :data:`SPOKEN_TEXT_LIMIT_VALID`. The API layer translates this into
    HTTP 422 with the full canonical list in the error body.
    """
    if value not in SPOKEN_TEXT_LIMIT_VALID:
        raise ValueError(f"invalid spoken text limit: {value!r}")
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETTINGS_KEY, str(value)),
        )
    return value


__all__ = [
    "DEFAULT",
    "SPOKEN_TEXT_LIMIT_VALID",
    "get_spoken_text_limit",
    "set_spoken_text_limit",
]
