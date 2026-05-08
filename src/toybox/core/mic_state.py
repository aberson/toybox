"""Mic mute / unmute state.

Companion to :mod:`toybox.core.listening`. Stores ``settings.mic_enabled``
(TEXT, ``"1"``/``"0"``). Defaults to ``"1"`` (mic on) when the row is
absent — the kiosk is privacy-by-design but a fresh boot should record
unless the operator explicitly mutes.

Per [plan.md §Listening Modes / Mic-hot indicator] mic_enabled is
independent of listening_mode: muting stops transcript persistence + ws
emit; listening_mode gates AI escalation. The two controls are
orthogonal so the parent can mute briefly (visitors over) without
losing their listening-mode preference.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from ..ws.envelope import Envelope, build_envelope
from ..ws.topics import Topic

_logger = logging.getLogger(__name__)

_SETTINGS_KEY = "mic_enabled"
_FALLBACK_DEFAULT = True

Publisher = Callable[[Envelope], None]


def current_mic_enabled(conn: sqlite3.Connection) -> bool:
    """Return the persisted mic-enabled flag, defaulting to True."""
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (_SETTINGS_KEY,),
    ).fetchone()
    if row is None:
        return _FALLBACK_DEFAULT
    raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    if raw in ("1", "true", "True"):
        return True
    if raw in ("0", "false", "False"):
        return False
    _logger.warning(
        "settings.%s=%r unparseable; falling back to %s",
        _SETTINGS_KEY,
        raw,
        _FALLBACK_DEFAULT,
    )
    return _FALLBACK_DEFAULT


def set_mic_enabled(
    conn: sqlite3.Connection,
    enabled: bool,
    publisher: Publisher | None = None,
) -> bool:
    """Persist ``enabled`` and emit a ``mic.enabled`` envelope."""
    canonical = bool(enabled)
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETTINGS_KEY, "1" if canonical else "0"),
        )
    if publisher is not None:
        envelope = build_envelope(
            topic=Topic.mic_enabled,
            payload={"enabled": canonical},
        )
        publisher(envelope)
    return canonical


__all__ = ["Publisher", "current_mic_enabled", "set_mic_enabled"]
