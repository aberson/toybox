"""Household-scoped transcript retention setting.

Companion to the Phase I sweep + filter-on-read added in I2. Stores
``settings.transcript_retention_seconds`` (TEXT, one of ``{60, 180, 300,
600, 900}``) and defaults to ``60`` when the row is missing, value is
unparseable, or the parsed integer is outside the canonical set.
Migration 0010 seeds the row on first run; legacy DBs that predate the
seed still resolve cleanly without an explicit migration step.

The value is read fresh per sweep tick and per API read in I2, so the
operator can flip the preset from SettingsPanel and have the next tick
honour it without a backend restart.

**Pipeline timestamp format (load-bearing for I2):** the production
audio pipeline writes ``transcripts.ended_at`` via
``_isoformat(ts)`` in :mod:`toybox.audio.pipeline` — see
:func:`toybox.audio.pipeline._isoformat`, which renders
``ts.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00",
"Z")``. The shape is documented in :data:`ENDED_AT_ISO_FORMAT_NOTE`
below so the I2 sweep helper + tests can emit byte-identical strings;
lexicographic string comparison against ``ended_at`` only matches
numeric comparison when formats are pinned.
"""

from __future__ import annotations

import logging
import sqlite3

_logger = logging.getLogger(__name__)


ENDED_AT_ISO_FORMAT_NOTE: str = (
    "transcripts.ended_at is written by toybox.audio.pipeline._isoformat "
    "as `ts.astimezone(UTC).isoformat(timespec=\"seconds\").replace(\"+00:00\", \"Z\")`. "
    "Concretely: UTC instant, second precision (NO microseconds, NO fractional "
    "seconds), trailing literal `Z` (NOT `+00:00`), 20 chars total — example "
    "`2026-05-10T15:23:45Z`. The I2 sweep + filter-on-read must format the "
    "cutoff the same way so that `ended_at < cutoff` lexicographic comparison "
    "matches numeric comparison against the underlying instant. If you change "
    "this format on the pipeline side, change it here and in every retention "
    "test that hard-codes a fixture timestamp in lockstep."
)


RETENTION_SECONDS_VALID: frozenset[int] = frozenset({60, 180, 300, 600, 900})
RETENTION_SECONDS_DEFAULT: int = 60

_SETTINGS_KEY = "transcript_retention_seconds"


def current_retention_seconds(conn: sqlite3.Connection) -> int:
    """Return the persisted retention window in seconds, defaulting to 60.

    Falls back to :data:`RETENTION_SECONDS_DEFAULT` in three cases:

    1. The settings row is absent (legacy DBs that predate migration
       0010, or a deleted seed row).
    2. The value cannot be parsed as ``int`` (corrupt blob, hand-edit).
    3. The parsed integer is not in :data:`RETENTION_SECONDS_VALID`
       (preset list shrunk, or a free-form value snuck in).

    Cases 2 and 3 log at WARNING with the offending value truncated to
    64 chars (mirrors :mod:`toybox.core.image_gen_mode` to keep a corrupt
    blob from flooding the logs).
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (_SETTINGS_KEY,),
    ).fetchone()
    if row is None:
        return RETENTION_SECONDS_DEFAULT
    raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        truncated = raw if isinstance(raw, str) and len(raw) <= 64 else f"{str(raw)[:64]}..."
        _logger.warning(
            "settings.%s=%r unparseable as int; falling back to %d",
            _SETTINGS_KEY,
            truncated,
            RETENTION_SECONDS_DEFAULT,
        )
        return RETENTION_SECONDS_DEFAULT
    if parsed not in RETENTION_SECONDS_VALID:
        truncated = raw if isinstance(raw, str) and len(raw) <= 64 else f"{str(raw)[:64]}..."
        _logger.warning(
            "settings.%s=%r outside canonical set; falling back to %d",
            _SETTINGS_KEY,
            truncated,
            RETENTION_SECONDS_DEFAULT,
        )
        return RETENTION_SECONDS_DEFAULT
    return parsed


def set_retention_seconds(conn: sqlite3.Connection, seconds: int) -> int:
    """Persist ``seconds`` and return the canonical int.

    Raises :class:`ValueError` when ``seconds`` is not in
    :data:`RETENTION_SECONDS_VALID`. The API layer translates this into
    HTTP 422 with the full canonical list in the error body.
    """
    if seconds not in RETENTION_SECONDS_VALID:
        raise ValueError(f"invalid retention seconds: {seconds!r}")
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETTINGS_KEY, str(seconds)),
        )
    return seconds


__all__ = [
    "ENDED_AT_ISO_FORMAT_NOTE",
    "RETENTION_SECONDS_DEFAULT",
    "RETENTION_SECONDS_VALID",
    "current_retention_seconds",
    "set_retention_seconds",
]
