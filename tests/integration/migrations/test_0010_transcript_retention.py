"""Coverage for Phase I Step I1 migration 0010 (transcript retention).

Pins:

* Fresh DB → seed row ``settings.transcript_retention_seconds = '60'``
  is present and the ``idx_transcripts_ended_at`` index exists.
* Populated DB → applying 0010 leaves existing transcript rows
  untouched, and ``INSERT OR IGNORE`` does not overwrite a pre-existing
  retention value.
* Migration is idempotent — running it a second time is a no-op.

Test pattern mirrors :mod:`tests.integration.migrations.test_0009_banned_themes`:
apply migrations 1..9 against a private staging dir, seed state, then
run the real migration runner to bring in 0010.
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


def _apply_pre_i1(tmp_path: Path) -> Path:
    """Apply migrations 1..9 to a fresh DB and return its path.

    Stages the pre-0010 .sql files into a private directory so the
    runner sees a frozen "before" snapshot, then we drive 0010 in via
    the real package directory.
    """
    pre_dir = tmp_path / "pre_i1_migrations"
    pre_dir.mkdir()
    available = discover_migrations()
    pre_i1: list[Migration] = [m for m in available if m.version <= 9]
    for m in pre_i1:
        (pre_dir / m.filename).write_text(m.path.read_text(encoding="utf-8"), encoding="utf-8")

    db_path = tmp_path / "toybox.db"
    conn = connect(db_path)
    try:
        run_migrations(conn, directory=pre_dir)
        assert current_version(conn) == 9
    finally:
        conn.close()
    return db_path


@pytest.fixture
def fresh_conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Connection where every migration (1..N) has run on a fresh DB."""
    c = connect(tmp_path / "toybox.db")
    try:
        run_migrations(c)
        yield c
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Fresh-DB shape
# ---------------------------------------------------------------------------


def test_0010_seeds_default_retention_value(fresh_conn: sqlite3.Connection) -> None:
    """Fresh DB after all migrations → seed row is present with value ``'60'``."""
    row = fresh_conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        ("transcript_retention_seconds",),
    ).fetchone()
    assert row is not None
    # Stored as the stringified default — the helper parses int() on read.
    assert row["value"] == "60"


def test_0010_creates_ended_at_index(fresh_conn: sqlite3.Connection) -> None:
    """Fresh DB → ``idx_transcripts_ended_at`` index exists on the table."""
    indexes = [
        row["name"]
        for row in fresh_conn.execute("PRAGMA index_list('transcripts')")
    ]
    assert "idx_transcripts_ended_at" in indexes


def test_0010_is_idempotent(fresh_conn: sqlite3.Connection) -> None:
    """A second run is a no-op — already-applied migrations don't replay."""
    starting = current_version(fresh_conn)
    second = run_migrations(fresh_conn)
    assert second == []
    assert current_version(fresh_conn) == starting

    # Both side effects are still present after the no-op re-run.
    row = fresh_conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        ("transcript_retention_seconds",),
    ).fetchone()
    assert row is not None
    assert row["value"] == "60"

    indexes = [
        row["name"]
        for row in fresh_conn.execute("PRAGMA index_list('transcripts')")
    ]
    assert indexes.count("idx_transcripts_ended_at") == 1


# ---------------------------------------------------------------------------
# Populated-DB application
# ---------------------------------------------------------------------------


def test_0010_preserves_existing_transcript_rows(tmp_path: Path) -> None:
    """A transcript row inserted before 0010 survives the migration intact."""
    db_path = _apply_pre_i1(tmp_path)
    conn = connect(db_path)
    try:
        # Seed a session + transcript row using the canonical pipeline
        # ISO format (second precision, trailing Z) so the row matches
        # what production would write.
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                ("sess-1", "2026-05-10T15:00:00Z"),
            )
            conn.execute(
                "INSERT INTO transcripts "
                "(id, session_id, started_at, ended_at, text, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "t-pre-0010",
                    "sess-1",
                    "2026-05-10T15:00:00Z",
                    "2026-05-10T15:00:02Z",
                    "hello toybox",
                    0.95,
                ),
            )

        applied = run_migrations(conn)
        assert any(m.version == 10 for m in applied)

        # Existing row is untouched.
        row = conn.execute(
            "SELECT id, session_id, text, ended_at FROM transcripts WHERE id = ?",
            ("t-pre-0010",),
        ).fetchone()
        assert row is not None
        assert row["text"] == "hello toybox"
        assert row["ended_at"] == "2026-05-10T15:00:02Z"

        # Seed exists.
        seed = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            ("transcript_retention_seconds",),
        ).fetchone()
        assert seed is not None
        assert seed["value"] == "60"

        # Index exists.
        indexes = [
            row["name"]
            for row in conn.execute("PRAGMA index_list('transcripts')")
        ]
        assert "idx_transcripts_ended_at" in indexes
    finally:
        conn.close()


def test_0010_does_not_overwrite_pre_existing_retention_value(
    tmp_path: Path,
) -> None:
    """``INSERT OR IGNORE`` preserves an operator-chosen retention value.

    The pre-0010 settings table already supports inserting arbitrary
    keys — a household that hand-set their retention before migration
    landed (unlikely, but possible during a staged rollout) should not
    have their preference clobbered.
    """
    db_path = _apply_pre_i1(tmp_path)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("transcript_retention_seconds", "900"),
            )

        run_migrations(conn)

        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            ("transcript_retention_seconds",),
        ).fetchone()
        assert row is not None
        # ``INSERT OR IGNORE`` left the pre-existing value alone.
        assert row["value"] == "900"
    finally:
        conn.close()

