"""Proposed-queue cap (drop-oldest at five activities).

Whenever the API proposes a new activity, the server first looks at
how many activities are currently in state ``proposed``. If five are
already pending parent approval, the **oldest** is automatically
transitioned to ``dismissed`` to make room. Each auto-dismissal
emits an ``activity.state`` envelope so connected UIs see the
disappearance.

Cap is process-local: a single uvicorn worker is the project
invariant.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

PROPOSED_QUEUE_CAP = 5
PROPOSED_STATE = "proposed"
DISMISSED_STATE = "dismissed"


def proposed_count(conn: sqlite3.Connection) -> int:
    """Return how many activities are currently in ``proposed``."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM activities WHERE state = ?",
        (PROPOSED_STATE,),
    ).fetchone()
    return int(row["n"])


def oldest_proposed_ids(
    conn: sqlite3.Connection,
    limit: int,
) -> list[str]:
    """Return up to ``limit`` ``proposed`` activity ids, oldest first."""
    if limit <= 0:
        return []
    rows = conn.execute(
        "SELECT id FROM activities WHERE state = ? ORDER BY created_at ASC, id ASC LIMIT ?",
        (PROPOSED_STATE, limit),
    ).fetchall()
    return [str(r["id"]) for r in rows]


def evict_oldest_for_capacity(
    conn: sqlite3.Connection,
    *,
    cap: int = PROPOSED_QUEUE_CAP,
) -> list[str]:
    """Drop oldest ``proposed`` activities until count is below ``cap``.

    Each auto-dismissed activity has its ``state`` set to ``dismissed``
    and ``version`` incremented atomically. Returns the list of
    activity ids that were dismissed (in the order they were dropped),
    so the caller can fan out an ``activity.state`` envelope per id.
    """
    current = proposed_count(conn)
    if current < cap:
        return []
    overflow = current - cap + 1
    victims = oldest_proposed_ids(conn, overflow)
    return list(_dismiss_ids(conn, victims))


def _dismiss_ids(conn: sqlite3.Connection, ids: Iterable[str]) -> Iterable[str]:
    with conn:
        for activity_id in ids:
            cur = conn.execute(
                "UPDATE activities SET state = ?, version = version + 1 WHERE id = ? AND state = ?",
                (DISMISSED_STATE, activity_id, PROPOSED_STATE),
            )
            if cur.rowcount == 1:
                yield activity_id


__all__ = [
    "DISMISSED_STATE",
    "PROPOSED_QUEUE_CAP",
    "PROPOSED_STATE",
    "evict_oldest_for_capacity",
    "oldest_proposed_ids",
    "proposed_count",
]
