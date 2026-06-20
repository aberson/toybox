"""Household-scoped parent-involvement dial setting.

Phase W Step W1 true-stub. Stores ``settings.parent_involvement`` (TEXT,
one of ``{"low", "medium", "high"}``) and defaults to ``"medium"`` when
the row is missing or the stored value is outside the canonical set.
Migration 0024 seeds the row on first run; legacy DBs that predate the
seed still resolve cleanly via the fallback.

PERSIST ONLY: nothing reads this value yet. A later phase consumes it to
tune how much the parent is asked to participate in an activity.
"""

from __future__ import annotations

import logging
import sqlite3

_logger = logging.getLogger(__name__)


PARENT_INVOLVEMENT_VALID: frozenset[str] = frozenset({"low", "medium", "high"})
DEFAULT: str = "medium"

_SETTINGS_KEY = "parent_involvement"


def get_parent_involvement(conn: sqlite3.Connection) -> str:
    """Return the persisted parent-involvement dial, defaulting to "medium".

    Falls back to :data:`DEFAULT` in two cases:

    1. The settings row is absent (legacy DBs that predate migration
       0024, or a deleted seed row).
    2. The stored value is not in :data:`PARENT_INVOLVEMENT_VALID`
       (preset list shrunk, or a free-form value snuck in).

    Case 2 logs at WARNING with the offending value truncated to 64
    chars (mirrors :mod:`toybox.core.spoken_text_limit`).
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (_SETTINGS_KEY,),
    ).fetchone()
    if row is None:
        return DEFAULT
    raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    if raw not in PARENT_INVOLVEMENT_VALID:
        truncated = raw if isinstance(raw, str) and len(raw) <= 64 else f"{str(raw)[:64]}..."
        _logger.warning(
            "settings.%s=%r outside canonical set; falling back to %r",
            _SETTINGS_KEY,
            truncated,
            DEFAULT,
        )
        return DEFAULT
    return str(raw)


def set_parent_involvement(conn: sqlite3.Connection, value: str) -> str:
    """Persist ``value`` and return the canonical string.

    Raises :class:`ValueError` when ``value`` is not in
    :data:`PARENT_INVOLVEMENT_VALID`. The API layer translates this into
    HTTP 422 with the full canonical list in the error body.
    """
    if value not in PARENT_INVOLVEMENT_VALID:
        raise ValueError(f"invalid parent involvement: {value!r}")
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETTINGS_KEY, value),
        )
    return value


__all__ = [
    "DEFAULT",
    "PARENT_INVOLVEMENT_VALID",
    "get_parent_involvement",
    "set_parent_involvement",
]
