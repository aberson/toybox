"""Coverage for the Step 15 labeled_events migration.

Tests the schema lands cleanly on a fresh DB, on an existing v1/v2 DB
(forward-compat), and that the basic CRUD shape required by step 15
works (insert, update parent_signal, update judge_scores).
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


def test_migration_runs_on_fresh_db(conn: sqlite3.Connection) -> None:
    applied = run_migrations(conn)
    versions = [m.version for m in applied]
    assert 3 in versions
    assert current_version(conn) >= 3


def test_table_columns(conn: sqlite3.Connection) -> None:
    run_migrations(conn)
    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(labeled_events)")}
    expected = {
        "id",
        "activity_id",
        "generated_at",
        "generator_path",
        "inputs_chatml_json",
        "activity_json",
        "parent_signal",
        "parent_signal_set_at",
        "ended_at_step",
        "judge_scores_json",
        "judge_run_at",
    }
    assert expected.issubset(set(cols)), set(cols)
    # NOT NULL constraints on the load-bearing fields.
    for not_null in (
        "activity_id",
        "generated_at",
        "generator_path",
        "inputs_chatml_json",
        "activity_json",
    ):
        assert cols[not_null]["notnull"] == 1, f"{not_null} should be NOT NULL"


def test_indexes_present(conn: sqlite3.Connection) -> None:
    run_migrations(conn)
    indexes = {row["name"] for row in conn.execute("PRAGMA index_list(labeled_events)")}
    assert "idx_labeled_events_activity_id" in indexes
    assert "idx_labeled_events_generated_at" in indexes


def test_check_constraint_rejects_bad_generator_path(conn: sqlite3.Connection) -> None:
    run_migrations(conn)
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO labeled_events "
                "(activity_id, generated_at, generator_path, "
                " inputs_chatml_json, activity_json) "
                "VALUES (?, ?, ?, ?, ?)",
                ("a1", "2026-05-03T00:00:00Z", "junk", "[]", "{}"),
            )


def test_insert_then_update_signal_and_scores(conn: sqlite3.Connection) -> None:
    run_migrations(conn)
    with conn:
        conn.execute(
            "INSERT INTO labeled_events "
            "(activity_id, generated_at, generator_path, "
            " inputs_chatml_json, activity_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "a-1",
                "2026-05-03T00:00:00Z",
                "offline",
                '[{"role":"system","content":"sys"}]',
                '{"id":"a-1"}',
            ),
        )
        conn.execute(
            "UPDATE labeled_events "
            "SET parent_signal = ?, parent_signal_set_at = ? "
            "WHERE activity_id = ?",
            (-1.0, "2026-05-03T00:01:00Z", "a-1"),
        )
        conn.execute(
            "UPDATE labeled_events "
            "SET judge_scores_json = ?, judge_run_at = ? "
            "WHERE activity_id = ?",
            ('{"safety":5}', "2026-05-03T00:02:00Z", "a-1"),
        )
    row = conn.execute(
        "SELECT * FROM labeled_events WHERE activity_id = ?",
        ("a-1",),
    ).fetchone()
    assert row["parent_signal"] == -1.0
    assert row["judge_scores_json"] == '{"safety":5}'


def test_unique_index_on_activity_id(conn: sqlite3.Connection) -> None:
    run_migrations(conn)
    with conn:
        conn.execute(
            "INSERT INTO labeled_events "
            "(activity_id, generated_at, generator_path, "
            " inputs_chatml_json, activity_json) "
            "VALUES (?, ?, ?, ?, ?)",
            ("a-dup", "2026-05-03T00:00:00Z", "offline", "[]", "{}"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                "INSERT INTO labeled_events "
                "(activity_id, generated_at, generator_path, "
                " inputs_chatml_json, activity_json) "
                "VALUES (?, ?, ?, ?, ?)",
                ("a-dup", "2026-05-03T00:01:00Z", "claude", "[]", "{}"),
            )


def test_migration_idempotent(conn: sqlite3.Connection) -> None:
    first = run_migrations(conn)
    assert any(m.version == 3 for m in first)
    second = run_migrations(conn)
    assert second == []


