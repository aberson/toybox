"""Coverage for the Phase K K1 migration 0014.

Pins:

* All three new columns are added to ``personas`` with the documented
  types + defaults.
* Pre-existing ``personas`` rows survive the migration with column
  defaults — ``role_weights = '{}'``, ``voice_profile IS NULL``,
  ``spontaneity_rates = '{"jokes":0.0,"songs":0.0}'``. This is the
  "custom persona" hydration path (acceptance #8).
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


def test_0014_adds_three_columns_with_documented_types(conn: sqlite3.Connection) -> None:
    applied = run_migrations(conn)
    versions = [m.version for m in applied]
    assert 14 in versions, versions
    assert current_version(conn) >= 14

    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(personas)")}

    assert "role_weights" in cols
    rw = cols["role_weights"]
    assert rw["notnull"] == 1, "role_weights must be NOT NULL"
    assert rw["type"].upper() == "TEXT"
    assert str(rw["dflt_value"]).strip("'") == "{}"

    assert "voice_profile" in cols
    vp = cols["voice_profile"]
    assert vp["notnull"] == 0, "voice_profile must be nullable"
    assert vp["type"].upper() == "TEXT"
    assert vp["dflt_value"] is None

    assert "spontaneity_rates" in cols
    sr = cols["spontaneity_rates"]
    assert sr["notnull"] == 1, "spontaneity_rates must be NOT NULL"
    assert sr["type"].upper() == "TEXT"
    # SQLite stores the literal default with single quotes; strip them for compare.
    default = str(sr["dflt_value"]).strip("'")
    assert default == '{"jokes":0.0,"songs":0.0}', default


def test_0014_is_idempotent(conn: sqlite3.Connection) -> None:
    run_migrations(conn)
    starting = current_version(conn)
    second = run_migrations(conn)
    assert second == []
    assert current_version(conn) == starting


def test_old_custom_personas_backfill_to_documented_defaults(tmp_path: Path) -> None:
    """A persona row INSERTed BEFORE migration 0014 ran ends up with
    ``role_weights = '{}'``, ``voice_profile IS NULL``,
    ``spontaneity_rates = '{"jokes":0.0,"songs":0.0}'`` after the
    migration applies.

    This is the acceptance #8 hydration path for existing custom
    (non-library) personas — they must not break when the new columns
    are added.
    """
    available = discover_migrations()
    pre_k1: list[Migration] = [m for m in available if m.version <= 13]
    assert any(m.version == 14 for m in available), "expected migration 0014 to be discoverable"

    pre_dir = tmp_path / "pre_migrations"
    pre_dir.mkdir()
    for m in pre_k1:
        (pre_dir / m.filename).write_text(m.path.read_text(encoding="utf-8"), encoding="utf-8")

    db_path = tmp_path / "toybox.db"
    conn = connect(db_path)
    try:
        run_migrations(conn, directory=pre_dir)
        assert current_version(conn) == 13

        with conn:
            conn.execute(
                "INSERT INTO personas "
                "(id, display_name, archetype, system_prompt, avatar_image_path, "
                " behavior_tags, age_range_min, age_range_max, language, source, "
                " default_voice_tone, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "custom_old",
                    "Custom Old Persona",
                    "custom",
                    "predates phase K",
                    None,
                    '["legacy"]',
                    3,
                    10,
                    "en",
                    "custom",
                    None,
                    "2026-05-09T00:00:00Z",
                ),
            )

        applied = run_migrations(conn)
        assert any(m.version == 14 for m in applied), [m.version for m in applied]

        row = conn.execute(
            "SELECT role_weights, voice_profile, spontaneity_rates FROM personas WHERE id = ?",
            ("custom_old",),
        ).fetchone()
        assert row is not None
        assert row["role_weights"] == "{}"
        assert row["voice_profile"] is None
        assert row["spontaneity_rates"] == '{"jokes":0.0,"songs":0.0}'
    finally:
        conn.close()


def test_new_persona_rows_can_supply_explicit_json(conn: sqlite3.Connection) -> None:
    """A persona INSERTed AFTER 0014 can supply explicit JSON for each column."""
    run_migrations(conn)
    with conn:
        conn.execute(
            "INSERT INTO personas "
            "(id, display_name, archetype, system_prompt, avatar_image_path, "
            " behavior_tags, age_range_min, age_range_max, language, source, "
            " default_voice_tone, created_at, role_weights, voice_profile, "
            " spontaneity_rates) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "k_explicit",
                "Explicit",
                "custom",
                "K1 round-trip",
                None,
                '["explicit"]',
                3,
                10,
                "en",
                "custom",
                None,
                "2026-05-14T00:00:00Z",
                '{"friend":1.5}',
                # Phase Z Z3: neural_voice rides in the same JSON column
                # (no SQL migration) — pin that the column stores it as-is.
                '{"neural_voice":"af_heart","pitch":1.0,"rate":1.0}',
                '{"jokes":0.1,"songs":0.2}',
            ),
        )

    row = conn.execute(
        "SELECT role_weights, voice_profile, spontaneity_rates FROM personas WHERE id = ?",
        ("k_explicit",),
    ).fetchone()
    assert row is not None
    assert row["role_weights"] == '{"friend":1.5}'
    assert row["voice_profile"] == '{"neural_voice":"af_heart","pitch":1.0,"rate":1.0}'
    assert row["spontaneity_rates"] == '{"jokes":0.1,"songs":0.2}'
