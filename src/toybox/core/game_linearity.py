"""Household-scoped game-linearity dial setting.

Phase W Step W2. Stores ``settings.game_linearity`` (TEXT, one of
``{"linear", "nonlinear"}``) and defaults to ``"nonlinear"`` when the
row is missing or the stored value is outside the canonical set.
Migration 0025 seeds the row on first run; legacy DBs that predate the
seed still resolve cleanly via the fallback.

WIRED: unlike the W1 true-stub dials, this value IS consumed. The
propose path in :mod:`toybox.api.activities` reads it and passes
``linear_only=(game_linearity == "linear")`` into
:func:`toybox.activities.generator.generate`, which excludes any
template that contains a branching step (a step with ``choices``) when
``linear`` is selected.
"""

from __future__ import annotations

import logging
import sqlite3

_logger = logging.getLogger(__name__)


GAME_LINEARITY_VALID: frozenset[str] = frozenset({"linear", "nonlinear"})
DEFAULT: str = "nonlinear"

_SETTINGS_KEY = "game_linearity"


def get_game_linearity(conn: sqlite3.Connection) -> str:
    """Return the persisted game-linearity dial, defaulting to "nonlinear".

    Falls back to :data:`DEFAULT` in two cases:

    1. The settings row is absent (legacy DBs that predate migration
       0025, or a deleted seed row).
    2. The stored value is not in :data:`GAME_LINEARITY_VALID`
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
    if raw not in GAME_LINEARITY_VALID:
        truncated = raw if isinstance(raw, str) and len(raw) <= 64 else f"{str(raw)[:64]}..."
        _logger.warning(
            "settings.%s=%r outside canonical set; falling back to %r",
            _SETTINGS_KEY,
            truncated,
            DEFAULT,
        )
        return DEFAULT
    return str(raw)


def set_game_linearity(conn: sqlite3.Connection, value: str) -> str:
    """Persist ``value`` and return the canonical string.

    Raises :class:`ValueError` when ``value`` is not in
    :data:`GAME_LINEARITY_VALID`. The API layer translates this into
    HTTP 422 with the full canonical list in the error body.
    """
    if value not in GAME_LINEARITY_VALID:
        raise ValueError(f"invalid game linearity: {value!r}")
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETTINGS_KEY, value),
        )
    return value


__all__ = [
    "DEFAULT",
    "GAME_LINEARITY_VALID",
    "get_game_linearity",
    "set_game_linearity",
]
