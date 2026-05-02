"""Tests for the initial v1 migration."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.db.connection import connect
from toybox.db.migrations import current_version, run_migrations

EXPECTED_TABLES = {
    "toys",
    "personas",
    "children",
    "rooms",
    "room_features",
    "activities",
    "activity_steps",
    "feedback",
    "transcripts",
    "sessions",
    "auth_tokens",
    "settings",
    "schema_migrations",
}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "toybox.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a connection and close it on teardown.

    Closing matters on Windows: ``tmp_path`` cleanup runs ``shutil.rmtree``
    which can fail with ``PermissionError: [WinError 32]`` while the
    sqlite handle is holding WAL/SHM files.
    """
    c = connect(db_path)
    try:
        yield c
    finally:
        c.close()


def test_runner_creates_all_expected_tables(conn: sqlite3.Connection) -> None:
    applied = run_migrations(conn)

    assert [m.filename for m in applied] == ["0001_initial.sql"]
    assert current_version(conn) == 1

    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r["name"] for r in rows if not r["name"].startswith("sqlite_")}
    assert EXPECTED_TABLES.issubset(names), f"missing: {EXPECTED_TABLES - names}"


def test_runner_is_idempotent(conn: sqlite3.Connection) -> None:
    first = run_migrations(conn)
    second = run_migrations(conn)
    assert len(first) == 1
    assert second == []
    assert current_version(conn) == 1


def test_runner_records_filename_and_applied_at(conn: sqlite3.Connection) -> None:
    run_migrations(conn)
    row = conn.execute(
        "SELECT filename, applied_at FROM schema_migrations WHERE version=1"
    ).fetchone()
    assert row["filename"] == "0001_initial.sql"
    assert row["applied_at"]


def test_crash_mid_migration_rolls_back_and_retries(tmp_path: Path) -> None:
    """If a migration's second statement fails, the first must roll back.

    Regression test for the ``executescript()`` bug: that helper issues an
    implicit COMMIT before running and runs each statement in autocommit
    mode, so partial state would persist on failure and the next run
    would die on ``table already exists``. This test pins the explicit
    transaction-per-migration contract.
    """
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    bad_file = migrations_dir / "9999_breaks_midway.sql"
    # Second statement targets a table that doesn't exist; sqlite must
    # raise OperationalError, the explicit BEGIN must roll back, and
    # table `a` must NOT be persisted to disk.
    bad_file.write_text(
        "CREATE TABLE a (x INTEGER);\nINSERT INTO nonexistent_table VALUES (1);\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "crash.db"

    conn = connect(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            run_migrations(conn, directory=migrations_dir)
    finally:
        conn.close()

    # Reopen and verify nothing partial landed.
    conn = connect(db_path)
    try:
        names = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "a" not in names, "partial CREATE TABLE leaked past ROLLBACK"
        applied_versions = [
            r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
        ]
        assert 9999 not in applied_versions, "failed migration was recorded as applied"
    finally:
        conn.close()

    # Fix the file and confirm a retry succeeds.
    bad_file.write_text("CREATE TABLE a (x INTEGER);\n", encoding="utf-8")
    conn = connect(db_path)
    try:
        applied = run_migrations(conn, directory=migrations_dir)
        assert [m.version for m in applied] == [9999]
        names = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "a" in names
        assert current_version(conn) == 9999
    finally:
        conn.close()
