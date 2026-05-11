"""Household-global banned-themes setting.

Sibling of :mod:`toybox.core.image_gen_mode`. Stores
``settings.banned_themes_global`` (TEXT, free-form comma-separated
list) and returns ``None`` when the row is missing — "no global ban
list" is a legitimate state, not an error to coerce to a default.

This replaces the per-child ``children.banned_themes`` column dropped
by migration 0009 (Phase H Step H4). The escalation pipeline already
UNION-aggregated banned themes across every child before sending the
prompt to Claude (see
:func:`toybox.activities.content_resolver.aggregate_child_constraints`
and ``escalation.py:870``); migration 0009 makes that aggregation the
single canonical value rather than recomputing it per request.

Readers should call :func:`current_banned_themes_global` once per
request scope (not per child) and treat ``None`` as "no constraint".
:func:`set_banned_themes_global` accepts ``None`` or an empty/whitespace
string to clear the value (DELETE the row), and any non-empty string is
persisted verbatim — normalisation (split/trim/dedupe/lowercase) is the
caller's job for display, not storage; the value the operator types in
the parent UI is round-tripped back without surprise.

No WebSocket broadcast: the consumer is the per-request escalation
path, not a long-lived worker that needs a push update like
:mod:`toybox.image_gen.worker`. The next propose picks up the new
value automatically; if a future multi-parent surface needs real-time
sync, add a ``banned_themes.global`` envelope topic then.
"""

from __future__ import annotations

import sqlite3

_SETTINGS_KEY = "banned_themes_global"


def current_banned_themes_global(conn: sqlite3.Connection) -> str | None:
    """Return the persisted global banned-themes string, or ``None``.

    ``None`` means "the operator has not set a household ban list" —
    treat it as an empty constraint set. Callers that need a sequence
    of normalised tokens should split-and-trim the returned string
    themselves; storage preserves the operator's exact input.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (_SETTINGS_KEY,),
    ).fetchone()
    if row is None:
        return None
    raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    if raw is None:
        return None
    if not isinstance(raw, str):
        # Defensive: settings.value is TEXT, but a hand-edited DB could
        # have a non-string entry. Return ``None`` rather than raising
        # so the dispatcher degrades to "no constraint" cleanly.
        return None
    return raw


def set_banned_themes_global(
    conn: sqlite3.Connection,
    value: str | None,
) -> None:
    """Persist ``value`` (or clear the row when empty/None).

    ``None`` or any string that is empty after :py:meth:`str.strip`
    deletes the row, which :func:`current_banned_themes_global` then
    reads back as ``None``. Otherwise the value is stored verbatim
    (no normalisation) so the operator's exact textarea contents
    round-trip cleanly.
    """
    if value is None or not value.strip():
        with conn:
            conn.execute(
                "DELETE FROM settings WHERE key = ?",
                (_SETTINGS_KEY,),
            )
        return
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETTINGS_KEY, value),
        )


__all__ = [
    "current_banned_themes_global",
    "set_banned_themes_global",
]
