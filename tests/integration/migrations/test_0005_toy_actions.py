"""Coverage for the Phase F Step F3 ``toy_actions`` migration (0005).

Pins the schema, verifies forward + idempotent application, and asserts
the FK ``ON DELETE CASCADE`` from ``toys.id`` actually fires when a toy
is hard-deleted (the soft-archive flow doesn't fire the cascade —
storage.toy_actions.delete_for_toy_archived is the seam there).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.db.connection import connect
from toybox.db.migrations import current_version, run_migrations


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "toybox.db")
    try:
        yield c
    finally:
        c.close()


def test_0005_migration_forward_and_idempotent(conn: sqlite3.Connection) -> None:
    """Apply 0001..0005 against a fresh DB; verify schema; re-apply is a no-op.

    Pins:
      * ``toy_actions`` table exists with the seven expected columns.
      * Composite primary key ``(toy_id, slot)`` lands.
      * ``ON DELETE CASCADE`` FK to ``toys.id`` lands.
      * Both supporting indexes land.
      * Re-running migrations after they've all applied returns ``[]``
        and leaves the schema untouched.
    """
    applied = run_migrations(conn)
    versions = [m.version for m in applied]
    assert 5 in versions, versions
    assert current_version(conn) >= 5

    # --- Columns -------------------------------------------------------
    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(toy_actions)")}
    assert set(cols.keys()) == {
        "toy_id",
        "slot",
        "status",
        "image_path",
        "seed",
        "error_msg",
        "updated_at",
    }, sorted(cols.keys())
    # NOT NULL columns per the migration text.
    assert cols["toy_id"]["notnull"] == 1
    assert cols["slot"]["notnull"] == 1
    assert cols["status"]["notnull"] == 1
    assert cols["updated_at"]["notnull"] == 1
    # Optional columns.
    assert cols["image_path"]["notnull"] == 0
    assert cols["seed"]["notnull"] == 0
    assert cols["error_msg"]["notnull"] == 0
    # Affinities.
    assert cols["seed"]["type"].upper() == "INTEGER"
    assert cols["status"]["type"].upper() == "TEXT"

    # --- Composite primary key ----------------------------------------
    # PRAGMA table_info reports ``pk`` as the position in the PK (1, 2,
    # ...) for every column that is part of the PK, 0 otherwise.
    pk_cols = {name for name, row in cols.items() if row["pk"] >= 1}
    assert pk_cols == {"toy_id", "slot"}

    # --- Foreign key (toys.id) with ON DELETE CASCADE -----------------
    fks = list(conn.execute("PRAGMA foreign_key_list(toy_actions)"))
    assert len(fks) == 1, fks
    fk = fks[0]
    assert fk["table"] == "toys"
    assert fk["from"] == "toy_id"
    assert fk["to"] == "id"
    assert fk["on_delete"].upper() == "CASCADE"

    # --- Indexes ------------------------------------------------------
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(toy_actions)")
        if row["origin"] == "c"  # ``c`` = explicitly CREATE INDEX'd.
    }
    assert "idx_toy_actions_status" in indexes
    assert "idx_toy_actions_toy_id_status" in indexes

    # --- Idempotence --------------------------------------------------
    starting_version = current_version(conn)
    second = run_migrations(conn)
    assert second == []
    assert current_version(conn) == starting_version

    cols_again = {row["name"]: row for row in conn.execute("PRAGMA table_info(toy_actions)")}
    assert set(cols_again.keys()) == set(cols.keys())


def test_foreign_key_cascade_on_toys_delete(conn: sqlite3.Connection) -> None:
    """Hard-deleting a toy must cascade-delete its ``toy_actions`` rows.

    The production soft-archive flow uses ``UPDATE toys SET archived = 1``
    which leaves rows on disk; the cascade is a defensive backstop for
    code paths that hard-delete (admin / test cleanup). This test pins
    that the FK + ``ON DELETE CASCADE`` are both wired.
    """
    run_migrations(conn)

    # Insert a toy + three action rows for it.
    toy_id = "550e8400-e29b-41d4-a716-446655440000"
    other_toy_id = "660e8400-e29b-41d4-a716-446655440111"
    with conn:
        conn.execute(
            "INSERT INTO toys (id, display_name, image_path, image_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (toy_id, "Bunny", "data/images/toys/bunny.jpg", "h1", "2026-05-06T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO toys (id, display_name, image_path, image_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (other_toy_id, "Wizard", "data/images/toys/wiz.jpg", "h2", "2026-05-06T00:00:00Z"),
        )
        for slot, status in (("idle", "queued"), ("pointing", "running"), ("looking", "done")):
            conn.execute(
                "INSERT INTO toy_actions "
                "(toy_id, slot, status, updated_at) VALUES (?, ?, ?, ?)",
                (toy_id, slot, status, "2026-05-06T00:00:00Z"),
            )
        # A row for the OTHER toy — should survive the cascade.
        conn.execute(
            "INSERT INTO toy_actions "
            "(toy_id, slot, status, updated_at) VALUES (?, ?, ?, ?)",
            (other_toy_id, "idle", "queued", "2026-05-06T00:00:00Z"),
        )

    pre = conn.execute(
        "SELECT COUNT(*) AS n FROM toy_actions WHERE toy_id = ?",
        (toy_id,),
    ).fetchone()
    assert pre["n"] == 3

    # Hard-delete the toy. ``foreign_keys = ON`` is enabled by
    # ``toybox.db.connection.connect`` so the cascade fires.
    with conn:
        conn.execute("DELETE FROM toys WHERE id = ?", (toy_id,))

    post = conn.execute(
        "SELECT COUNT(*) AS n FROM toy_actions WHERE toy_id = ?",
        (toy_id,),
    ).fetchone()
    assert post["n"] == 0, "ON DELETE CASCADE did not fire"

    # The OTHER toy's rows must survive — the cascade is scoped.
    other_post = conn.execute(
        "SELECT COUNT(*) AS n FROM toy_actions WHERE toy_id = ?",
        (other_toy_id,),
    ).fetchone()
    assert other_post["n"] == 1
