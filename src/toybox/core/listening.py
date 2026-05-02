"""Listening-mode state machine.

Mode persists in ``settings.listening_mode`` (TEXT, parsed to int). Mode
changes emit a typed envelope on the ``listening.mode`` ws topic so the
parent and child apps reflect the slider position instantly.

Step 4 ships the contract only — the actual mic, STT, and Claude wiring
that interprets each mode lands in Phase B (steps 11-14). The publisher
parameter on :func:`set_mode` is a callable rather than an imported hub
because the broadcast machinery doesn't exist until Step 8; tests pass a
list-collecting stub, production will pass the hub once it lands.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable
from enum import IntEnum

from ..ws.envelope import Envelope, build_envelope
from ..ws.topics import Topic

_logger = logging.getLogger(__name__)

_SETTINGS_KEY = "listening_mode"
_DEFAULT_MODE_ENV = "TOYBOX_DEFAULT_MODE"
_FALLBACK_DEFAULT = 3


class ListeningMode(IntEnum):
    """Five-position parent-controlled cadence slider (see plan §Listening Modes)."""

    OFFLINE = 1
    LOW = 2
    DEFAULT = 3
    HIGH = 4
    INTENSE = 5


Publisher = Callable[[Envelope], None]


def _env_default_mode() -> ListeningMode:
    raw = os.environ.get(_DEFAULT_MODE_ENV)
    if raw is None:
        return ListeningMode(_FALLBACK_DEFAULT)
    try:
        value = int(raw)
    except ValueError:
        _logger.warning(
            "%s=%r is not an int; falling back to %d",
            _DEFAULT_MODE_ENV,
            raw,
            _FALLBACK_DEFAULT,
        )
        return ListeningMode(_FALLBACK_DEFAULT)
    if value not in {m.value for m in ListeningMode}:
        _logger.warning(
            "%s=%d outside 1-5; falling back to %d",
            _DEFAULT_MODE_ENV,
            value,
            _FALLBACK_DEFAULT,
        )
        return ListeningMode(_FALLBACK_DEFAULT)
    return ListeningMode(value)


def current_mode(conn: sqlite3.Connection) -> ListeningMode:
    """Return the persisted listening mode.

    Reads ``settings.listening_mode`` (TEXT) and parses to int. Step 2's
    migration seeds the row to ``'3'`` at install time, so the
    ``TOYBOX_DEFAULT_MODE`` fallback only triggers on (a) a fresh DB
    where the migration hasn't run yet, or (b) someone deleted the row
    out from under us — both of which warrant a WARNING log.

    Raises:
        ValueError: If the persisted value parses to an int outside 1-5.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (_SETTINGS_KEY,),
    ).fetchone()
    if row is None:
        fallback = _env_default_mode()
        _logger.warning(
            "settings.%s missing; falling back to %s",
            _SETTINGS_KEY,
            fallback.name,
        )
        return fallback
    raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"settings.{_SETTINGS_KEY}={raw!r} is not a valid integer") from exc
    if value not in {m.value for m in ListeningMode}:
        raise ValueError(f"settings.{_SETTINGS_KEY}={value} outside the 1-5 ListeningMode range")
    return ListeningMode(value)


def set_mode(
    conn: sqlite3.Connection,
    mode: ListeningMode | int,
    publisher: Publisher | None = None,
) -> ListeningMode:
    """Persist ``mode`` to settings and emit a ``listening.mode`` envelope.

    Args:
        conn: Open SQLite connection.
        mode: Integer 1-5 or :class:`ListeningMode` member.
        publisher: Optional callable invoked with the constructed
            :class:`Envelope`. ``None`` means "no broadcast" — tests
            assert against a list-collecting stub; production will pass
            the Step 8 ws hub.

    Returns:
        The canonicalized :class:`ListeningMode`.

    Raises:
        ValueError: If ``mode`` is not in 1-5.
    """
    try:
        canonical = ListeningMode(int(mode))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid listening mode {mode!r}; expected 1-5") from exc

    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETTINGS_KEY, str(canonical.value)),
        )

    if publisher is not None:
        envelope = build_envelope(
            topic=Topic.listening_mode,
            payload={"mode": canonical.value},
        )
        publisher(envelope)

    return canonical


__all__ = ["ListeningMode", "Publisher", "current_mode", "set_mode"]
