"""Household-scoped play-cadence-seconds setting.

Governs how often the autonomous play queue ticks when proposing the
next play step. Stores ``settings.play_cadence_seconds`` (TEXT, one of
``{0, 10, 30, 60}``) and defaults to ``30`` when the row is missing,
value is unparseable, or the parsed integer is outside the canonical
set. Migration 0012 seeds the row on first run; legacy DBs that
predate the seed still resolve cleanly via the defensive fallback.

**``0`` is a valid in-set value meaning "cadence disabled" — NOT a
sentinel for unset.** Implementations must use explicit
``value in PLAY_CADENCE_SECONDS_VALID`` set membership, never a
truthiness check like ``if value:``, or a legitimate operator choice
of "disabled" would silently coerce back to the default.

Mirrors :mod:`toybox.core.transcript_retention` for shape — same
defensive-read fallback, same set-membership validation, same UPSERT
semantics. Public API:

- :data:`PLAY_CADENCE_SECONDS_DEFAULT` — the int returned on absent /
  corrupt / out-of-set rows.
- :data:`PLAY_CADENCE_SECONDS_VALID` — frozenset of accepted ints
  (includes ``0``).
- :func:`get` — defensive read with fallback.
- :func:`set` — validated UPSERT; raises :class:`ValueError` on bad input.
"""

from __future__ import annotations

import logging
import sqlite3

_logger = logging.getLogger(__name__)


PLAY_CADENCE_SECONDS_VALID: frozenset[int] = frozenset({0, 10, 30, 60})
PLAY_CADENCE_SECONDS_DEFAULT: int = 30

_SETTINGS_KEY = "play_cadence_seconds"


def get(conn: sqlite3.Connection) -> int:
    """Return the persisted play cadence in seconds, defaulting to 30.

    Falls back to :data:`PLAY_CADENCE_SECONDS_DEFAULT` in three cases:

    1. The settings row is absent (legacy DBs that predate migration
       0012, or a deleted seed row).
    2. The value cannot be parsed as ``int`` (corrupt blob, hand-edit).
    3. The parsed integer is not in :data:`PLAY_CADENCE_SECONDS_VALID`
       (preset list shrunk, or a free-form value snuck in). ``0`` is
       in the canonical set, so a persisted ``'0'`` round-trips as
       ``0`` — not the default.

    Cases 2 and 3 log at WARNING with the offending value truncated to
    64 chars — mirrors :mod:`toybox.core.transcript_retention` so a
    corrupt blob can't flood the logs.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (_SETTINGS_KEY,),
    ).fetchone()
    if row is None:
        return PLAY_CADENCE_SECONDS_DEFAULT
    raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        truncated = raw if isinstance(raw, str) and len(raw) <= 64 else f"{str(raw)[:64]}..."
        _logger.warning(
            "settings.%s=%r unparseable as int; falling back to %d",
            _SETTINGS_KEY,
            truncated,
            PLAY_CADENCE_SECONDS_DEFAULT,
        )
        return PLAY_CADENCE_SECONDS_DEFAULT
    # Explicit set-membership — NOT a truthiness check. ``0`` is valid.
    if parsed not in PLAY_CADENCE_SECONDS_VALID:
        truncated = raw if isinstance(raw, str) and len(raw) <= 64 else f"{str(raw)[:64]}..."
        _logger.warning(
            "settings.%s=%r outside canonical set; falling back to %d",
            _SETTINGS_KEY,
            truncated,
            PLAY_CADENCE_SECONDS_DEFAULT,
        )
        return PLAY_CADENCE_SECONDS_DEFAULT
    return parsed


def set(conn: sqlite3.Connection, value: int) -> int:  # noqa: A001 -- mirrors test surface
    """Persist ``value`` and return the canonical int.

    Raises :class:`ValueError` when ``value`` is not in
    :data:`PLAY_CADENCE_SECONDS_VALID`. ``0`` is a valid in-set value
    — do NOT shortcut on ``if not value``. The API layer translates
    the ValueError into HTTP 422 with the full canonical list in the
    error body, mirroring :mod:`toybox.api.transcript_retention_settings`.
    """
    # Explicit set-membership — NOT a truthiness check. ``0`` is valid.
    if value not in PLAY_CADENCE_SECONDS_VALID:
        raise ValueError(f"invalid play cadence seconds: {value!r}")
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETTINGS_KEY, str(value)),
        )
    return value


__all__ = [
    "PLAY_CADENCE_SECONDS_DEFAULT",
    "PLAY_CADENCE_SECONDS_VALID",
    "get",
    "set",
]
