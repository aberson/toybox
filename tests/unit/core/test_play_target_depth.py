"""Unit coverage for :mod:`toybox.core.play_target_depth`.

Household-scoped ``play_target_depth`` setting. Canonical set is
``{1, 3, 5}`` with default ``3``. Mirrors the ``transcript_retention``
testing pattern — same fixture, same defensive fallback assertions,
same set-rejection assertions.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.core.play_target_depth import (
    PLAY_TARGET_DEPTH_DEFAULT,
    PLAY_TARGET_DEPTH_VALID,
    get,
    set,
)
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations

_SETTINGS_KEY = "play_target_depth"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a fresh, migrated connection; close on teardown (Windows-safe)."""
    conn = connect(tmp_path / "toybox.db")
    try:
        run_migrations(conn)
        yield conn
    finally:
        conn.close()


def test_get_returns_default_on_fresh_db(db: sqlite3.Connection) -> None:
    """Fresh migrated DB → ``get`` returns the canonical default (3)."""
    assert PLAY_TARGET_DEPTH_DEFAULT == 3
    assert PLAY_TARGET_DEPTH_VALID == frozenset({1, 3, 5})
    assert get(db) == PLAY_TARGET_DEPTH_DEFAULT


def test_set_persists_and_round_trips(db: sqlite3.Connection) -> None:
    """Every canonical preset round-trips cleanly through set → get."""
    set(db, 5)
    assert get(db) == 5

    set(db, 1)
    assert get(db) == 1

    set(db, 3)
    assert get(db) == 3


def test_set_rejects_out_of_set_value(db: sqlite3.Connection) -> None:
    """Out-of-set value raises ValueError and leaves the persisted row untouched."""
    # Seed a known-good value so we can prove the failed set didn't mutate the row.
    set(db, 5)
    assert get(db) == 5

    with pytest.raises(ValueError):
        set(db, 2)

    # DB row must be unchanged — failed set is a true no-op on persisted state.
    assert get(db) == 5

    # A few more invalid samples to lock the rejection surface in.
    for invalid in [0, -1, 4, 6, 100]:
        with pytest.raises(ValueError):
            set(db, invalid)
    assert get(db) == 5


def test_get_falls_back_to_default_on_out_of_set_stored_value(
    db: sqlite3.Connection,
) -> None:
    """Hand-edited out-of-set stored value → ``get`` returns the default, not the bad value."""
    with db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETTINGS_KEY, "7"),
        )

    assert get(db) == PLAY_TARGET_DEPTH_DEFAULT


def test_get_falls_back_to_default_on_missing_row(db: sqlite3.Connection) -> None:
    """Absent settings row → silent fallback to the default."""
    with db:
        db.execute("DELETE FROM settings WHERE key = ?", (_SETTINGS_KEY,))

    assert get(db) == PLAY_TARGET_DEPTH_DEFAULT
