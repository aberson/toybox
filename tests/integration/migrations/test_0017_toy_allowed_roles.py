"""Coverage for migration 0017 — per-toy ``allowed_roles`` column.

Pins:

* The ``allowed_roles`` column is added to ``toys`` as nullable TEXT
  (the JSON-encoded list shape — see migration comment).
* Pre-existing ``toys`` rows survive the migration with the column
  defaulting to ``NULL`` (i.e. canonical "unrestricted").
* New rows can supply a JSON-encoded list literal and round-trip.
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


def test_0017_adds_allowed_roles_column(conn: sqlite3.Connection) -> None:
    applied = run_migrations(conn)
    versions = [m.version for m in applied]
    assert 17 in versions, versions
    assert current_version(conn) >= 17

    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(toys)")}
    assert "allowed_roles" in cols
    col = cols["allowed_roles"]
    assert col["notnull"] == 0, "allowed_roles must be nullable (NULL = unrestricted)"
    assert col["type"].upper() == "TEXT"
    # No default — the canonical "unrestricted" sentinel is NULL itself.
    assert col["dflt_value"] is None


def test_0017_is_idempotent(conn: sqlite3.Connection) -> None:
    run_migrations(conn)
    starting = current_version(conn)
    second = run_migrations(conn)
    assert second == []
    assert current_version(conn) == starting


def test_old_toys_backfill_to_null_allowed_roles(tmp_path: Path) -> None:
    """A toy INSERTed BEFORE migration 0017 ran ends up with
    ``allowed_roles IS NULL`` after the migration applies — backwards
    compatible with every existing row in the catalog.
    """
    available = discover_migrations()
    pre_k_post: list[Migration] = [m for m in available if m.version <= 16]
    assert any(m.version == 17 for m in available), "expected migration 0017 to be discoverable"

    pre_dir = tmp_path / "pre_migrations"
    pre_dir.mkdir()
    for m in pre_k_post:
        (pre_dir / m.filename).write_text(m.path.read_text(encoding="utf-8"), encoding="utf-8")

    db_path = tmp_path / "toybox.db"
    conn = connect(db_path)
    try:
        run_migrations(conn, directory=pre_dir)
        assert current_version(conn) == 16

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
        assert any(m.version == 17 for m in applied), [m.version for m in applied]

        row = conn.execute(
            "SELECT allowed_roles FROM toys WHERE id = ?",
            ("legacy_toy",),
        ).fetchone()
        assert row is not None
        assert row["allowed_roles"] is None
    finally:
        conn.close()


def test_new_toys_can_supply_json_encoded_list(conn: sqlite3.Connection) -> None:
    """After migration 0017 a fresh toy row can carry a JSON list of
    role-name strings in ``allowed_roles`` and round-trip the literal
    column value byte-for-byte."""
    run_migrations(conn)
    with conn:
        conn.execute(
            "INSERT INTO toys "
            "(id, display_name, image_path, image_hash, type, tags, "
            " persona_id, archived, created_at, last_used_at, allowed_roles) "
            "VALUES (?, ?, ?, ?, NULL, NULL, NULL, 0, "
            " '2026-01-01T00:00:00Z', NULL, ?)",
            ("bowser", "Bowser", "img/bowser.png", "hash-bowser", '["big_bad_boss"]'),
        )

    row = conn.execute(
        "SELECT allowed_roles FROM toys WHERE id = ?",
        ("bowser",),
    ).fetchone()
    assert row is not None
    assert row["allowed_roles"] == '["big_bad_boss"]'
