"""Coverage for migration 0018 — per-toy ``active`` column.

Pins:

* The ``active`` column is added to ``toys`` as NOT NULL INTEGER with
  a default of 1 (= active) so every existing row stays in play after
  the migration.
* Pre-existing ``toys`` rows survive the migration with the column
  defaulting to 1.
* New rows can supply ``active = 0`` explicitly and round-trip.
* Migration is forward + idempotent.
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


def test_0018_adds_active_column(conn: sqlite3.Connection) -> None:
    applied = run_migrations(conn)
    versions = [m.version for m in applied]
    assert 18 in versions, versions
    assert current_version(conn) >= 18

    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(toys)")}
    assert "active" in cols
    col = cols["active"]
    assert col["notnull"] == 1, "active must be NOT NULL"
    assert col["type"].upper() == "INTEGER"
    # SQLite stores the DEFAULT verbatim as the literal in the SQL.
    assert str(col["dflt_value"]) == "1"


def test_0018_is_idempotent(conn: sqlite3.Connection) -> None:
    run_migrations(conn)
    starting = current_version(conn)
    second = run_migrations(conn)
    assert second == []
    assert current_version(conn) == starting


def test_old_toys_backfill_to_active_1(tmp_path: Path) -> None:
    """A toy INSERTed BEFORE migration 0018 ran ends up with
    ``active = 1`` after the migration applies — backwards compatible
    with every existing row in the catalog.
    """
    available = discover_migrations()
    pre_18: list[Migration] = [m for m in available if m.version <= 17]
    assert any(m.version == 18 for m in available), "expected migration 0018 to be discoverable"

    pre_dir = tmp_path / "pre_migrations"
    pre_dir.mkdir()
    for m in pre_18:
        (pre_dir / m.filename).write_text(m.path.read_text(encoding="utf-8"), encoding="utf-8")

    db_path = tmp_path / "toybox.db"
    conn = connect(db_path)
    try:
        run_migrations(conn, directory=pre_dir)
        assert current_version(conn) == 17

        with conn:
            conn.execute(
                "INSERT INTO toys "
                "(id, display_name, image_path, image_hash, type, tags, "
                " persona_id, archived, created_at, last_used_at) "
                "VALUES (?, ?, ?, ?, NULL, NULL, NULL, 0, "
                " '2026-01-01T00:00:00Z', NULL)",
                ("legacy_toy", "Legacy Bear", "img/legacy.png", "hash-legacy"),
            )

        applied = run_migrations(conn)
        assert any(m.version == 18 for m in applied), [m.version for m in applied]

        row = conn.execute(
            "SELECT active FROM toys WHERE id = ?",
            ("legacy_toy",),
        ).fetchone()
        assert row is not None
        assert row["active"] == 1
    finally:
        conn.close()


def test_new_toys_can_supply_active_0(conn: sqlite3.Connection) -> None:
    """After migration 0018 a fresh toy row can be inserted with
    ``active = 0`` and round-trip the integer value."""
    run_migrations(conn)
    with conn:
        conn.execute(
            "INSERT INTO toys "
            "(id, display_name, image_path, image_hash, type, tags, "
            " persona_id, archived, created_at, last_used_at, active) "
            "VALUES (?, ?, ?, ?, NULL, NULL, NULL, 0, "
            " '2026-01-01T00:00:00Z', NULL, 0)",
            ("muted", "Muted Cat", "img/muted.png", "hash-muted"),
        )

    row = conn.execute(
        "SELECT active FROM toys WHERE id = ?",
        ("muted",),
    ).fetchone()
    assert row is not None
    assert row["active"] == 0
