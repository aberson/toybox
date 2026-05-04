"""Dynamic trigger sources.

A "dynamic" trigger source is one whose patterns are derived from runtime
state — currently just the rows of the ``toys`` table. Step 6 ships the
v1 stub: rebuild the toy list on every ``match()`` call. Phase C step 15
will replace this with an event-driven cache invalidated on toy CRUD.

The match must be word-boundary-aware (``\\b``), case-insensitive, and
not match substrings inside longer words. We delegate that to the
:mod:`re` engine: each toy display name is escaped and bracketed with
``\\b...\\b`` so ``"Mr. Unicorn"`` matches ``"i love mr. unicorn"`` but
not ``"unicornium"``.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass

_logger = logging.getLogger(__name__)

MENTION_TOY_INTENT = "mention_toy"
TOY_PATTERN_ID_PREFIX = "dyn_toy_"
TOY_PATTERN_VERSION = 1


@dataclass(frozen=True, slots=True)
class ToyTrigger:
    """One compiled dynamic trigger derived from a ``toys`` row."""

    toy_id: str
    display_name: str
    pattern: re.Pattern[str]

    @property
    def pattern_id(self) -> str:
        """Stable id used in :class:`Intent.pattern_id` outputs."""
        return f"{TOY_PATTERN_ID_PREFIX}{self.toy_id}"


def _compile_for(display_name: str) -> re.Pattern[str] | None:
    stripped = display_name.strip()
    if not stripped:
        return None
    # ``re.escape`` handles dots, parens, and other punctuation safely.
    # We anchor on ``\b`` on both sides so we don't match substrings in
    # longer words. The IGNORECASE flag covers letter-case matching.
    return re.compile(rf"\b{re.escape(stripped)}\b", re.IGNORECASE)


def load_toy_triggers(conn: sqlite3.Connection) -> list[ToyTrigger]:
    """Return one :class:`ToyTrigger` per non-archived toy.

    Rebuilds on every call (v1 stub). Empty table → empty list. Rows
    whose ``display_name`` is empty after stripping are skipped with a
    debug log.
    """
    triggers: list[ToyTrigger] = []
    try:
        rows = list(
            conn.execute(
                "SELECT id, display_name FROM toys WHERE archived = 0 ORDER BY id"
            ).fetchall()
        )
    except sqlite3.DatabaseError as exc:
        _logger.warning("toys table query failed; no dynamic triggers: %s", exc)
        return []

    for row in rows:
        toy_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        display_name = row["display_name"] if isinstance(row, sqlite3.Row) else row[1]
        compiled = _compile_for(display_name)
        if compiled is None:
            _logger.debug("toy %s has empty display_name; skipped", toy_id)
            continue
        triggers.append(ToyTrigger(toy_id=toy_id, display_name=display_name, pattern=compiled))
    return triggers


def refresh_mention_toys(conn: sqlite3.Connection) -> int:
    """Re-read the dynamic toy-trigger list from the DB.

    Step 6's dynamic source rebuilds on every :func:`match` call, so
    there's no in-memory cache to invalidate today. This function
    exists as the documented entry-point step 16's toy ingest
    pipeline calls after committing a new toy: when step 15's
    event-driven cache lands, this is the single hook to add the
    cache-bust to. For now it just re-queries to confirm the new
    toy is visible and logs the count at INFO.

    Returns the number of active (non-archived) toy triggers after
    the refresh — useful for the integration test that asserts the
    new row is in scope.
    """
    triggers = load_toy_triggers(conn)
    _logger.info("refresh_mention_toys: %d active toy trigger(s)", len(triggers))
    return len(triggers)


__all__ = [
    "MENTION_TOY_INTENT",
    "TOY_PATTERN_ID_PREFIX",
    "TOY_PATTERN_VERSION",
    "ToyTrigger",
    "load_toy_triggers",
    "refresh_mention_toys",
]
