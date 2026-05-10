"""Coverage for the Phase G G2 ``activities.slot_fills_json`` migration (0008).

Pins:

* The new ``slot_fills_json TEXT NOT NULL DEFAULT '{}'`` column is
  added to ``activities``.
* Migration is forward + idempotent.
* Pre-existing ``activities`` rows survive the migration with
  ``slot_fills_json = '{}'`` — exactly what the lazy advance handler
  in G3 needs (in-flight activities have all their step bodies
  pre-rendered, so an empty fill map is the correct semantic).
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


def test_0008_adds_not_null_default_empty_object(conn: sqlite3.Connection) -> None:
    applied = run_migrations(conn)
    versions = [m.version for m in applied]
    assert 8 in versions, versions
    assert current_version(conn) >= 8

    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(activities)")}
    assert "slot_fills_json" in cols
    col = cols["slot_fills_json"]
    assert col["notnull"] == 1, "slot_fills_json must be NOT NULL"
    assert col["type"].upper() == "TEXT"
    # SQLite stores the literal default with quotes for a string default.
    # Accept the canonical encoding ``'{}'`` (what 0008 declares).
    assert str(col["dflt_value"]).strip("'") in ("{}", '{}')

    # Idempotency.
    starting = current_version(conn)
    second = run_migrations(conn)
    assert second == []
    assert current_version(conn) == starting


def test_old_activities_default_to_empty_object(tmp_path: Path) -> None:
    """A row INSERTed BEFORE migration 0008 ran ends up with
    ``slot_fills_json = '{}'`` after the migration applies. This is
    the correct semantic for in-flight activities at upgrade time —
    their pre-seeded step bodies already have rendered fills, so
    the advance handler doesn't need a fills map for them.
    """
    available = discover_migrations()
    pre_g2_slot: list[Migration] = [m for m in available if m.version <= 7]
    assert any(m.version == 8 for m in available), "expected migration 0008 to be discoverable"

    pre_dir = tmp_path / "pre_migrations"
    pre_dir.mkdir()
    for m in pre_g2_slot:
        (pre_dir / m.filename).write_text(m.path.read_text(encoding="utf-8"), encoding="utf-8")

    db_path = tmp_path / "toybox.db"
    conn = connect(db_path)
    try:
        run_migrations(conn, directory=pre_dir)
        assert current_version(conn) == 7

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

        applied = run_migrations(conn)
        assert any(m.version == 8 for m in applied), [m.version for m in applied]

        row = conn.execute(
            "SELECT slot_fills_json FROM activities WHERE id = ?", ("a-old",)
        ).fetchone()
        assert row is not None
        assert row["slot_fills_json"] == "{}"
    finally:
        conn.close()


def test_new_activities_can_supply_explicit_slot_fills(conn: sqlite3.Connection) -> None:
    """An INSERT that explicitly sets ``slot_fills_json`` round-trips."""
    run_migrations(conn)
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            ("s1", "2026-05-09T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO activities "
            "(id, session_id, state, version, summary, intent_source, created_at, "
            " slot_fills_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "a1", "s1", "proposed", 1, None, "boredom", "2026-05-09T00:00:00Z",
                '{"adjective": "sparkly", "room": "kitchen", "toy": "Penguin"}',
            ),
        )
    row = conn.execute(
        "SELECT slot_fills_json FROM activities WHERE id = ?", ("a1",)
    ).fetchone()
    assert row is not None
    assert row["slot_fills_json"] == (
        '{"adjective": "sparkly", "room": "kitchen", "toy": "Penguin"}'
    )
