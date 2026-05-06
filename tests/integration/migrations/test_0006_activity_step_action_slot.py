"""Coverage for the Phase F Step F6 ``activity_steps.action_slot`` migration (0006).

Pins:

* The new ``action_slot TEXT`` column is added to ``activity_steps`` and
  is nullable (legacy rows default to NULL → kiosk renders no sprite,
  matching the pre-F6 behavior).
* Migration is forward + idempotent: re-running migrations after they've
  applied returns ``[]`` and leaves the schema untouched.
* Pre-existing ``activity_steps`` rows survive the migration with
  ``action_slot = NULL``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.db.connection import connect
from toybox.db.migrations import (
    Migration,
    current_version,
    discover_migrations,
    run_migrations,
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "toybox.db")
    try:
        yield c
    finally:
        c.close()


def test_0006_migration_forward_and_idempotent(conn: sqlite3.Connection) -> None:
    """Apply 0001..0006 against a fresh DB; verify schema; re-apply is a no-op."""
    applied = run_migrations(conn)
    versions = [m.version for m in applied]
    assert 6 in versions, versions
    assert current_version(conn) >= 6

    # --- Column exists, is nullable, has TEXT affinity ----------------
    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(activity_steps)")}
    assert "action_slot" in cols, sorted(cols.keys())
    action_slot_col = cols["action_slot"]
    assert action_slot_col["notnull"] == 0, "action_slot must be nullable"
    assert action_slot_col["type"].upper() == "TEXT"
    # No DEFAULT pin: the column declaration leaves dflt_value NULL,
    # which sqlite serializes as None for new rows where the INSERT
    # omits the column.
    assert action_slot_col["dflt_value"] is None

    # --- Pre-F6 columns still present ---------------------------------
    assert {"id", "activity_id", "seq", "body", "sfx", "expected_action", "current"}.issubset(
        cols.keys()
    )

    # --- Idempotence --------------------------------------------------
    starting_version = current_version(conn)
    second = run_migrations(conn)
    assert second == []
    assert current_version(conn) == starting_version

    cols_again = {row["name"]: row for row in conn.execute("PRAGMA table_info(activity_steps)")}
    assert set(cols_again.keys()) == set(cols.keys())


def test_action_slot_column_accepts_null_and_vocab_member(conn: sqlite3.Connection) -> None:
    """Insertions with NULL or a valid slot string both succeed.

    The DB column is unconstrained (no CHECK constraint) — vocabulary
    enforcement lives at the Pydantic + generator layer per plan §F6.
    The migration test pins the column shape, not the application-layer
    contract.
    """
    run_migrations(conn)

    # Set up a session + activity row so the FK on activity_steps holds.
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            ("sess-1", "2026-05-06T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO activities "
            "(id, session_id, state, version, summary, intent_source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("act-1", "sess-1", "proposed", 1, None, "boredom", "2026-05-06T00:00:00Z"),
        )
        # NULL action_slot.
        conn.execute(
            "INSERT INTO activity_steps "
            "(id, activity_id, seq, body, sfx, expected_action, current, action_slot) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("step-1", "act-1", 1, "step body 1", None, None, 0, None),
        )
        # Vocabulary member.
        conn.execute(
            "INSERT INTO activity_steps "
            "(id, activity_id, seq, body, sfx, expected_action, current, action_slot) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("step-2", "act-1", 2, "step body 2", None, None, 0, "pointing"),
        )

    rows = conn.execute(
        "SELECT seq, action_slot FROM activity_steps "
        "WHERE activity_id = ? ORDER BY seq ASC",
        ("act-1",),
    ).fetchall()
    assert [(int(r["seq"]), r["action_slot"]) for r in rows] == [
        (1, None),
        (2, "pointing"),
    ]


def test_old_rows_default_null_after_migration(tmp_path: Path) -> None:
    """A row inserted before migration 0006 ran must end up with action_slot=NULL.

    This pins the "old rows default NULL → kiosk renders no sprite"
    contract from the migration's docstring. We bring the DB up to
    version 5 first, insert a step row that doesn't know about
    ``action_slot``, then run the rest of the migrations and verify
    the existing row's value is NULL.
    """
    # Discover all migrations and split into pre-F6 (<=5) and the rest.
    available = discover_migrations()
    pre_f6: list[Migration] = [m for m in available if m.version <= 5]
    post_f6: list[Migration] = [m for m in available if m.version >= 6]
    assert pre_f6, "expected migrations 0001..0005 to be discoverable"
    assert any(m.version == 6 for m in post_f6), "expected migration 0006 to be discoverable"

    # Build a tmp migrations dir holding only the pre-F6 files so the
    # runner stops at version 5. discover_migrations + apply path drives
    # off the directory listing.
    pre_dir = tmp_path / "pre_migrations"
    pre_dir.mkdir()
    for m in pre_f6:
        (pre_dir / m.filename).write_text(m.path.read_text(encoding="utf-8"), encoding="utf-8")

    db_path = tmp_path / "toybox.db"
    conn = connect(db_path)
    try:
        run_migrations(conn, directory=pre_dir)
        assert current_version(conn) == 5

        # Insert a pre-F6-shape activity_steps row (no action_slot column
        # in the INSERT — it doesn't exist yet).
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                ("sess-old", "2026-05-06T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO activities "
                "(id, session_id, state, version, summary, intent_source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("act-old", "sess-old", "proposed", 1, None, "boredom", "2026-05-06T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO activity_steps "
                "(id, activity_id, seq, body, sfx, expected_action, current) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("step-old", "act-old", 1, "old body", None, None, 0),
            )

        # Now apply migration 0006 from the package's own directory.
        applied = run_migrations(conn)
        assert any(m.version == 6 for m in applied), [m.version for m in applied]

        # Pre-existing row's action_slot column must be NULL.
        row = conn.execute(
            "SELECT action_slot FROM activity_steps WHERE id = ?",
            ("step-old",),
        ).fetchone()
        assert row is not None
        assert row["action_slot"] is None
    finally:
        conn.close()
