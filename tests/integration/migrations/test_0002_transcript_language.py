"""Coverage for the Step 13 transcript-language migration."""

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


def test_language_column_added(conn: sqlite3.Connection) -> None:
    run_migrations(conn)
    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(transcripts)")}
    assert "language" in cols, list(cols)
    assert cols["language"]["notnull"] == 1
    # SQLite returns the default verbatim from the CREATE/ALTER statement.
    assert cols["language"]["dflt_value"] == "'unknown'"


def test_language_defaults_to_unknown_on_insert(conn: sqlite3.Connection) -> None:
    run_migrations(conn)
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            ("s1", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO transcripts "
            "(id, session_id, started_at, ended_at, text, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("t1", "s1", "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z", "hi", 0.5),
        )
    row = conn.execute("SELECT language FROM transcripts WHERE id = 't1'").fetchone()
    assert row["language"] == "unknown"


def test_migration_recorded(conn: sqlite3.Connection) -> None:
    applied = run_migrations(conn)
    versions = [m.version for m in applied]
    # This file's contract: migration 1 must run before migration 2 (the
    # runner applies in order and records each in schema_migrations).
    # The previous "versions == sorted(versions)" check was vacuous —
    # `[]`, `[2]`, and `[1, 2, 3]` all satisfy it. Pin the file's actual
    # contract: both 1 and 2 are recorded.
    assert 1 in versions
    assert 2 in versions
    assert current_version(conn) >= 2


def test_migration_is_idempotent_on_second_run(conn: sqlite3.Connection) -> None:
    """Re-running migrations after they've all applied must be a no-op.

    Pins the runner's idempotent contract: every migration on disk is
    recorded on the first call, and a second call returns ``[]``. The
    ``language`` column survives intact (still present, still populated
    with the default for new inserts) — proving the second call did not
    re-execute the ``ALTER TABLE`` (which would itself fail with
    ``duplicate column name``).
    """
    first = run_migrations(conn)
    versions = [m.version for m in first]
    assert 2 in versions
    starting_version = current_version(conn)
    assert starting_version >= 2

    second = run_migrations(conn)
    assert second == []
    assert current_version(conn) == starting_version

    # ``language`` column still present + still defaults to ``unknown``.
    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(transcripts)")}
    assert "language" in cols
    assert cols["language"]["dflt_value"] == "'unknown'"
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            ("s-idem", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO transcripts "
            "(id, session_id, started_at, ended_at, text, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("t-idem", "s-idem", "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z", "hi", 0.5),
        )
    row = conn.execute(
        "SELECT language FROM transcripts WHERE id = 't-idem'"
    ).fetchone()
    assert row["language"] == "unknown"
