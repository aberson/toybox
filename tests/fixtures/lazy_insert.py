"""Test helpers for Phase G G2 lazy-insertion regressions.

Phase G G2 switched activity creation from "INSERT all 5 template
steps up-front" to "INSERT only ``steps[0]``; G3's advance handler
inserts the rest as the kid progresses". The G2 step does NOT ship
the lazy advance handler — that's G3's deliverable. So pre-G3,
post-G2 NEW activities can only advance ONE step before the existing
``post_advance`` handler trips its terminal condition (``next_index
>= len(steps)``).

This module provides ``backfill_legacy_steps`` — a test helper that
re-INSERTs steps 2..N directly into ``activity_steps`` so the
existing ``post_advance`` handler can walk through them. This
simulates the "in-flight pre-G2 activity at upgrade time" shape
that the Phase G plan explicitly mandates must keep advancing
correctly: the operator pulls latest, runs migrations, and the
in-flight activities (which already have all 5 step rows from
their pre-G2 insertion) continue to advance as today.

Lifecycle tests that exercise multi-step advance (state transitions,
step-back, completed-from-running) use this helper to seed the
legacy 5-row shape on top of the G2 propose response. New G2
behavior (lazy single-row insert + ``slot_fills_json`` populated)
is covered by dedicated tests in
``tests/unit/activities/test_generator.py``; G3 covers post-G2
multi-step advance once the lazy handler ships.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Sequence


def backfill_legacy_steps(
    conn: sqlite3.Connection,
    activity_id: str,
    *,
    bodies: Sequence[str] | None = None,
) -> None:
    """Insert pre-G2-shape rows for steps 2..N into ``activity_steps``.

    The G2 propose path inserts only ``steps[0]`` with ``current=1``.
    This helper appends 4 more rows (seqs 2..5) so the resulting
    activity has the legacy 5-row shape. Subsequent rows are
    ``current=0`` per the pre-G2 contract — only the row at the
    current ``seq`` is flagged.

    ``bodies`` overrides the per-row body text; defaults to a small
    placeholder so existing tests that don't read the body don't
    have to spell it out. The propose flow's ``steps[0]`` is left
    untouched.
    """
    placeholder = list(bodies) if bodies is not None else [f"legacy step {i}" for i in range(2, 6)]
    if len(placeholder) != 4:
        raise ValueError("backfill_legacy_steps expects exactly 4 bodies for seqs 2..5")
    with conn:
        for seq, body in zip(range(2, 6), placeholder, strict=True):
            conn.execute(
                "INSERT INTO activity_steps "
                "(id, activity_id, seq, body, sfx, expected_action, current, action_slot, "
                " choices_json, step_template_id) "
                "VALUES (?, ?, ?, ?, NULL, NULL, 0, NULL, NULL, NULL)",
                (str(uuid.uuid4()), activity_id, seq, body),
            )


__all__ = ["backfill_legacy_steps"]
