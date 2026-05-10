"""Coverage for the Phase G G2 ``activity_steps`` choices migration (0007).

Pins:

* The new ``chosen_label TEXT`` column is added to
  ``activity_steps`` and is nullable (legacy rows default NULL ⇒
  kiosk renders no recorded choice, matching pre-G2 behavior on
  the linear advance path).
* The new ``choices_json TEXT`` column is added and nullable.
* The new ``step_template_id TEXT`` column is added and nullable.
* Migration is forward + idempotent: re-running migrations is a
  no-op.
* Pre-existing ``activity_steps`` rows inserted at the pre-G2
  shape survive the migration with all three new columns NULL.
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


def test_0007_migration_adds_three_nullable_columns(conn: sqlite3.Connection) -> None:
    applied = run_migrations(conn)
    versions = [m.version for m in applied]
    assert 7 in versions, versions
    assert current_version(conn) >= 7

    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(activity_steps)")}
    for name in ("chosen_label", "choices_json", "step_template_id"):
        assert name in cols, f"missing G2 column {name!r}; got {sorted(cols.keys())}"
        col = cols[name]
        assert col["notnull"] == 0, f"{name} must be nullable"
        assert col["type"].upper() == "TEXT"
        assert col["dflt_value"] is None

    # Idempotency: re-running is a no-op.
    starting = current_version(conn)
    second = run_migrations(conn)
    assert second == []
    assert current_version(conn) == starting


def test_old_rows_default_null_on_all_three_g2_columns(tmp_path: Path) -> None:
    """A row INSERTed BEFORE migration 0007 ran must end up with the
    three new columns all NULL after the migration applies.

    Mirrors the pre-G2 in-flight activity at upgrade time: its
    ``activity_steps`` rows landed without knowledge of
    ``chosen_label``, ``choices_json``, or ``step_template_id``,
    and the migration must NOT backfill any of them (no surprising
    data, kiosk linear path stays identical).
    """
    available = discover_migrations()
    pre_g2: list[Migration] = [m for m in available if m.version <= 6]
    assert pre_g2, "expected pre-G2 migrations to be discoverable"
    assert any(m.version == 7 for m in available), "expected migration 0007 to be discoverable"

    pre_dir = tmp_path / "pre_migrations"
    pre_dir.mkdir()
    for m in pre_g2:
        (pre_dir / m.filename).write_text(m.path.read_text(encoding="utf-8"), encoding="utf-8")

    db_path = tmp_path / "toybox.db"
    conn = connect(db_path)
    try:
        run_migrations(conn, directory=pre_dir)
        assert current_version(conn) == 6

        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                ("s-old", "2026-05-09T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO activities "
                "(id, session_id, state, version, summary, intent_source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("a-old", "s-old", "proposed", 1, None, "boredom", "2026-05-09T00:00:00Z"),
            )
            # Pre-G2-shape INSERT: only the pre-0007 columns set.
            conn.execute(
                "INSERT INTO activity_steps "
                "(id, activity_id, seq, body, sfx, expected_action, current, action_slot) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("step-old", "a-old", 1, "old body", None, None, 0, None),
            )

        applied = run_migrations(conn)
        assert any(m.version == 7 for m in applied), [m.version for m in applied]

        row = conn.execute(
            "SELECT chosen_label, choices_json, step_template_id "
            "FROM activity_steps WHERE id = ?",
            ("step-old",),
        ).fetchone()
        assert row is not None
        assert row["chosen_label"] is None
        assert row["choices_json"] is None
        assert row["step_template_id"] is None
    finally:
        conn.close()


def test_g2_columns_accept_null_and_real_values(conn: sqlite3.Connection) -> None:
    """Insertions with NULL or actual values both succeed; no DB-level
    constraint on the values (vocabulary / validity is enforced at
    the Pydantic + generator + JSON-schema layer)."""
    run_migrations(conn)
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            ("s1", "2026-05-09T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO activities "
            "(id, session_id, state, version, summary, intent_source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("a1", "s1", "proposed", 1, None, "boredom", "2026-05-09T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO activity_steps "
            "(id, activity_id, seq, body, sfx, expected_action, current, action_slot, "
            " chosen_label, choices_json, step_template_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("st1", "a1", 1, "body", None, None, 1, "thinking",
             None, '["Sneak", "Charge"]', "open"),
        )
        conn.execute(
            "INSERT INTO activity_steps "
            "(id, activity_id, seq, body, sfx, expected_action, current, action_slot, "
             " chosen_label, choices_json, step_template_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("st2", "a1", 2, "body 2", None, None, 0, "looking",
             "Sneak", None, "sneak"),
        )
    rows = conn.execute(
        "SELECT seq, chosen_label, choices_json, step_template_id "
        "FROM activity_steps WHERE activity_id = ? ORDER BY seq",
        ("a1",),
    ).fetchall()
    assert [
        (int(r["seq"]), r["chosen_label"], r["choices_json"], r["step_template_id"])
        for r in rows
    ] == [
        (1, None, '["Sneak", "Charge"]', "open"),
        (2, "Sneak", None, "sneak"),
    ]
