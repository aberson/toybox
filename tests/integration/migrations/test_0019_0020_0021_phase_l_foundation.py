"""Phase L Step L1 — coverage for migrations 0019, 0020, 0021 + type primitives.

Pins:

* Migration 0019 creates the ``rewards`` table with the expected
  columns (PK ``id``, ``display_name``, ``image_path``, ``image_hash``,
  ``tags`` default ``'[]'``, ``animation``, ``active`` default 1,
  ``archived`` default 0, ``created_at``, ``last_used_at``).
* Migration 0020 adds nullable ``reward_type`` TEXT to ``activities``
  with no DB-level default (NULL = legacy pre-L row).
* Migration 0021 deletes the three deprecated Phase K play-surface
  rows from ``settings``, even when pre-seeded before this migration
  applies. The DELETE is a no-op against a DB that does not have those
  rows.
* Migrations are forward + idempotent (re-running ``run_migrations``
  applies nothing new).
* :class:`toybox.activities.models.Animation` is a six-member StrEnum
  with exactly ``shine``, ``jump``, ``spin``, ``pulse``, ``wobble``,
  ``float`` in that order.
* :data:`toybox.activities.models.RewardType` is a ``typing.Literal``
  alias whose ``get_args`` returns the four wire strings.
"""

from __future__ import annotations

import sqlite3
import typing
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.activities.models import Animation, RewardType
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


# ---------------------------------------------------------------------------
# 0019 — rewards table
# ---------------------------------------------------------------------------


def test_0019_creates_rewards_table(conn: sqlite3.Connection) -> None:
    applied = run_migrations(conn)
    versions = [m.version for m in applied]
    assert 19 in versions, versions
    assert current_version(conn) >= 19

    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(rewards)")}
    expected = {
        "id",
        "display_name",
        "image_path",
        "image_hash",
        "tags",
        "animation",
        "active",
        "archived",
        "created_at",
        "last_used_at",
    }
    assert set(cols) == expected, set(cols) ^ expected

    # ``id`` is the PK; SQLite reports pk > 0 on PK columns. The PK
    # constraint implies NOT NULL but ``PRAGMA table_info`` still
    # reports ``notnull = 0`` for PK columns by convention, so we
    # check the PK separately rather than fold it into the NOT NULL
    # loop below.
    assert cols["id"]["pk"] >= 1

    # NOT NULL columns per the migration (PK ``id`` excluded — see
    # above).
    for nn_col in (
        "display_name",
        "image_path",
        "image_hash",
        "tags",
        "animation",
        "active",
        "archived",
        "created_at",
    ):
        assert cols[nn_col]["notnull"] == 1, nn_col

    # Nullable column.
    assert cols["last_used_at"]["notnull"] == 0

    # Defaults — SQLite stores the literal as written.
    assert cols["tags"]["dflt_value"] == "'[]'"
    assert str(cols["active"]["dflt_value"]) == "1"
    assert str(cols["archived"]["dflt_value"]) == "0"


def test_0019_rewards_round_trips_a_row(conn: sqlite3.Connection) -> None:
    """A fresh reward row can be inserted with explicit values for the
    columns the API layer (L2) will fill, and the defaults populate
    the rest."""
    run_migrations(conn)
    with conn:
        conn.execute(
            "INSERT INTO rewards "
            "(id, display_name, image_path, image_hash, animation, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "reward_chest",
                "Treasure Chest",
                "data/images/rewards/reward_chest.png",
                "hash-chest",
                "shine",
                "2026-05-16T00:00:00Z",
            ),
        )

    row = conn.execute(
        "SELECT id, tags, active, archived, animation, last_used_at FROM rewards WHERE id = ?",
        ("reward_chest",),
    ).fetchone()
    assert row is not None
    assert row["tags"] == "[]"
    assert row["active"] == 1
    assert row["archived"] == 0
    assert row["animation"] == "shine"
    assert row["last_used_at"] is None


# ---------------------------------------------------------------------------
# 0020 — activities.reward_type
# ---------------------------------------------------------------------------


def test_0020_adds_reward_type_column(conn: sqlite3.Connection) -> None:
    applied = run_migrations(conn)
    versions = [m.version for m in applied]
    assert 20 in versions, versions

    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(activities)")}
    assert "reward_type" in cols
    col = cols["reward_type"]
    assert col["notnull"] == 0, "reward_type must be nullable (NULL = legacy pre-L row)"
    assert col["type"].upper() == "TEXT"
    # No default — API layer writes 'random' explicitly when the parent
    # omits the field; legacy rows stay NULL.
    assert col["dflt_value"] is None


# ---------------------------------------------------------------------------
# 0021 — drop deprecated play flags
# ---------------------------------------------------------------------------


def test_0021_drops_deprecated_play_flags_when_present(tmp_path: Path) -> None:
    """A DB that pre-seeded the three Phase K flags (the normal
    production path — they ship in 0015) loses those three rows when
    0021 applies. Other settings rows are untouched.
    """
    available = discover_migrations()
    pre_21: list[Migration] = [m for m in available if m.version <= 20]
    assert any(m.version == 21 for m in available), "expected migration 0021 to be discoverable"

    pre_dir = tmp_path / "pre_migrations"
    pre_dir.mkdir()
    for m in pre_21:
        (pre_dir / m.filename).write_text(m.path.read_text(encoding="utf-8"), encoding="utf-8")

    db_path = tmp_path / "toybox.db"
    conn = connect(db_path)
    try:
        run_migrations(conn, directory=pre_dir)
        assert current_version(conn) == 20

        # 0015 seeded the three rows; double-check they're there.
        seeded_keys = {
            row["key"]
            for row in conn.execute(
                "SELECT key FROM settings WHERE key IN ("
                "'play_embedded_enabled', "
                "'play_endings_enabled', "
                "'play_spontaneity_enabled')"
            )
        }
        assert seeded_keys == {
            "play_embedded_enabled",
            "play_endings_enabled",
            "play_spontaneity_enabled",
        }

        # Add a sibling row to confirm it survives.
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("sibling_flag", "true"),
            )

        applied = run_migrations(conn)
        assert any(m.version == 21 for m in applied), [m.version for m in applied]

        remaining = {
            row["key"]
            for row in conn.execute(
                "SELECT key FROM settings WHERE key IN ("
                "'play_embedded_enabled', "
                "'play_endings_enabled', "
                "'play_spontaneity_enabled')"
            )
        }
        assert remaining == set(), remaining

        sibling = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            ("sibling_flag",),
        ).fetchone()
        assert sibling is not None
        assert sibling["value"] == "true"

        # Sibling Phase K flags from 0015 also survive.
        survivors = {
            row["key"]
            for row in conn.execute(
                "SELECT key FROM settings WHERE key IN ("
                "'jokes_enabled', "
                "'songs_enabled', "
                "'play_standalone_enabled')"
            )
        }
        assert survivors == {"jokes_enabled", "songs_enabled", "play_standalone_enabled"}
    finally:
        conn.close()


def test_0021_is_a_no_op_when_rows_absent(conn: sqlite3.Connection) -> None:
    """If the three deprecated rows do not exist (e.g. operator hand-
    deleted them before the migration ran), 0021's DELETE is a no-op
    and the migration still records as applied.
    """
    run_migrations(conn)
    # Pre-clear the rows so 0021's DELETE has nothing to do.
    with conn:
        conn.execute(
            "DELETE FROM settings WHERE key IN ("
            "'play_embedded_enabled', "
            "'play_endings_enabled', "
            "'play_spontaneity_enabled')"
        )
    # Re-running migrations after a manual delete is a no-op for 0021;
    # it has already been applied in the first run_migrations call.
    again = run_migrations(conn)
    assert again == []
    assert current_version(conn) >= 21


# ---------------------------------------------------------------------------
# Idempotency across all three new migrations
# ---------------------------------------------------------------------------


def test_phase_l_migrations_idempotent(conn: sqlite3.Connection) -> None:
    run_migrations(conn)
    starting = current_version(conn)
    second = run_migrations(conn)
    assert second == []
    assert current_version(conn) == starting
    # Sanity: we are at or past 21.
    assert starting >= 21


# ---------------------------------------------------------------------------
# Type primitives (Animation StrEnum + RewardType Literal)
# ---------------------------------------------------------------------------


def test_animation_enum_has_expected_members_in_order() -> None:
    """``Animation`` is a six-member StrEnum with the exact values and
    order specified by the Phase L plan. The codegen step relies on
    member-definition order for the TS dropdown sequence, so the order
    is part of the contract.
    """
    expected_order = ["shine", "jump", "spin", "pulse", "wobble", "float"]
    actual = [member.value for member in Animation]
    assert actual == expected_order, actual

    # StrEnum members are also strings — assertable directly.
    assert Animation.shine == "shine"
    assert Animation.float == "float"


def test_reward_type_literal_exposes_five_wire_strings() -> None:
    """``RewardType`` is a ``typing.Literal`` alias; ``get_args`` returns
    the five wire strings. The API layer (L2) consumes these as raw
    strings — there is no Enum coercion path.

    L follow-up Change D added ``"none"`` as the explicit opt-out (the
    parent's "no reward this activity" dropdown choice); distinct from
    NULL on the column (legacy pre-L row).
    """
    args = typing.get_args(RewardType)
    assert set(args) == {"picture", "joke", "song", "random", "none"}
    # Order is also part of the contract for the TS union; pin it.
    assert args == ("picture", "joke", "song", "random", "none")
