"""Unit coverage for :mod:`toybox.core.banned_themes`.

Mirrors :mod:`tests.unit.core.test_image_gen_mode`. Pins the contract
that ``None`` is the canonical "no global ban list" return — NOT a
default value coerced from a missing row. Empty / whitespace-only PUT
values delete the row; non-empty values round-trip verbatim with no
normalisation (split/trim/lowercase is the caller's job for display).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.core.banned_themes import (
    current_banned_themes_global,
    set_banned_themes_global,
)
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a fresh, migrated connection; close on teardown."""
    conn = connect(tmp_path / "toybox.db")
    try:
        run_migrations(conn)
        yield conn
    finally:
        conn.close()


def test_current_returns_none_when_row_absent(db: sqlite3.Connection) -> None:
    """A fresh DB has no row for the global key → ``None``."""
    assert current_banned_themes_global(db) is None


def test_set_then_current_round_trip(db: sqlite3.Connection) -> None:
    set_banned_themes_global(db, "monsters, spiders")
    assert current_banned_themes_global(db) == "monsters, spiders"


def test_set_none_deletes_row(db: sqlite3.Connection) -> None:
    set_banned_themes_global(db, "monsters")
    set_banned_themes_global(db, None)
    assert current_banned_themes_global(db) is None
    row = db.execute(
        "SELECT COUNT(*) AS n FROM settings WHERE key = ?",
        ("banned_themes_global",),
    ).fetchone()
    assert row["n"] == 0


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_set_blank_string_deletes_row(db: sqlite3.Connection, blank: str) -> None:
    """An empty / whitespace-only value deletes the row."""
    set_banned_themes_global(db, "monsters")
    set_banned_themes_global(db, blank)
    assert current_banned_themes_global(db) is None


def test_set_value_round_trips_verbatim(db: sqlite3.Connection) -> None:
    """Storage preserves the operator's exact textarea contents."""
    value = "Monsters,  SPIDERS, ghosts  "
    set_banned_themes_global(db, value)
    assert current_banned_themes_global(db) == value


def test_set_overwrites_existing_value(db: sqlite3.Connection) -> None:
    set_banned_themes_global(db, "monsters")
    set_banned_themes_global(db, "ghosts")
    assert current_banned_themes_global(db) == "ghosts"
