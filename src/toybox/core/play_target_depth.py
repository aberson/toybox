"""Household-scoped play-target-depth setting.

Governs how deep the autonomous play queue aims to branch when
proposing the next play step. Stores ``settings.play_target_depth``
(TEXT, one of ``{1, 3, 5}``) and defaults to ``3`` when the row is
missing, value is unparseable, or the parsed integer is outside the
canonical set. Migration 0011 seeds the row on first run; legacy DBs
that predate the seed still resolve cleanly via the defensive fallback.

The value is read fresh per propose tick and per API read, so the
operator can flip the preset from the parent UI and have the next tick
honour it without a backend restart.

Mirrors :mod:`toybox.core.transcript_retention` for shape — same
defensive-read fallback, same set-membership validation, same UPSERT
semantics. Public API:

- :data:`PLAY_TARGET_DEPTH_DEFAULT` — the int returned on absent /
  corrupt / out-of-set rows.
- :data:`PLAY_TARGET_DEPTH_VALID` — frozenset of accepted ints.
- :func:`get` — defensive read with fallback.
- :func:`set` — validated UPSERT; raises :class:`ValueError` on bad input.
"""

from __future__ import annotations

import logging
import sqlite3

_logger = logging.getLogger(__name__)


PLAY_TARGET_DEPTH_VALID: frozenset[int] = frozenset({1, 3, 5})
PLAY_TARGET_DEPTH_DEFAULT: int = 3

_SETTINGS_KEY = "play_target_depth"


def get(conn: sqlite3.Connection) -> int:
    """Return the persisted play target depth, defaulting to 3.

    Falls back to :data:`PLAY_TARGET_DEPTH_DEFAULT` in three cases:

    1. The settings row is absent (legacy DBs that predate migration
       0011, or a deleted seed row).
    2. The value cannot be parsed as ``int`` (corrupt blob, hand-edit).
    3. The parsed integer is not in :data:`PLAY_TARGET_DEPTH_VALID`
       (preset list shrunk, or a free-form value snuck in).

    Cases 2 and 3 log at WARNING with the offending value truncated to
    64 chars — mirrors :mod:`toybox.core.transcript_retention` so a
    corrupt blob can't flood the logs.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (_SETTINGS_KEY,),
    ).fetchone()
    if row is None:
        return PLAY_TARGET_DEPTH_DEFAULT
    raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        truncated = raw if isinstance(raw, str) and len(raw) <= 64 else f"{str(raw)[:64]}..."
        _logger.warning(
            "settings.%s=%r unparseable as int; falling back to %d",
            _SETTINGS_KEY,
            truncated,
            PLAY_TARGET_DEPTH_DEFAULT,
        )
        return PLAY_TARGET_DEPTH_DEFAULT
    if parsed not in PLAY_TARGET_DEPTH_VALID:
        truncated = raw if isinstance(raw, str) and len(raw) <= 64 else f"{str(raw)[:64]}..."
        _logger.warning(
            "settings.%s=%r outside canonical set; falling back to %d",
            _SETTINGS_KEY,
            truncated,
            PLAY_TARGET_DEPTH_DEFAULT,
        )
        return PLAY_TARGET_DEPTH_DEFAULT
    return parsed


def set(conn: sqlite3.Connection, value: int) -> int:  # noqa: A001 -- mirrors test surface
    """Persist ``value`` and return the canonical int.

    Raises :class:`ValueError` when ``value`` is not in
    :data:`PLAY_TARGET_DEPTH_VALID`. The API layer translates this into
    HTTP 422 with the full canonical list in the error body, mirroring
    :mod:`toybox.api.transcript_retention_settings`.
    """
    if value not in PLAY_TARGET_DEPTH_VALID:
        raise ValueError(f"invalid play target depth: {value!r}")
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETTINGS_KEY, str(value)),
        )
    return value


__all__ = [
    "PLAY_TARGET_DEPTH_DEFAULT",
    "PLAY_TARGET_DEPTH_VALID",
    "get",
    "set",
]
