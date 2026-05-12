"""Unit coverage for :mod:`toybox.core.play_cadence_seconds`.

Household-scoped ``play_cadence_seconds`` setting. Canonical set is
``{0, 10, 30, 60}`` with default ``30``. ``0`` is a valid in-set value
meaning "cadence disabled" — NOT a sentinel for unset. Mirrors the
``transcript_retention`` testing pattern.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.core.play_cadence_seconds import (
    PLAY_CADENCE_SECONDS_DEFAULT,
    PLAY_CADENCE_SECONDS_VALID,
    get,
    set,
)
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations

_SETTINGS_KEY = "play_cadence_seconds"


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
    """Fresh migrated DB → ``get`` returns the canonical default (30)."""
    assert PLAY_CADENCE_SECONDS_DEFAULT == 30
    assert PLAY_CADENCE_SECONDS_VALID == frozenset({0, 10, 30, 60})
    assert get(db) == PLAY_CADENCE_SECONDS_DEFAULT


def test_set_persists_and_round_trips(db: sqlite3.Connection) -> None:
    """Every canonical non-zero preset round-trips cleanly through set → get."""
    set(db, 60)
    assert get(db) == 60

    set(db, 10)
    assert get(db) == 10

    set(db, 30)
    assert get(db) == 30


def test_set_zero_persists_and_round_trips(db: sqlite3.Connection) -> None:
    """``0`` is a valid in-set value ("cadence disabled"), NOT a sentinel for unset.

    Explicit regression guard: a naive implementation might treat ``0``
    as falsy and either reject it or short-circuit to the default. The
    behaviour spec requires the persisted row to be ``'0'`` and ``get``
    to return ``0`` exactly.
    """
    set(db, 0)
    assert get(db) == 0

    # And the row genuinely persists as '0' across a fresh read with a
    # value that would have been the default if the set silently dropped.
    set(db, 30)
    assert get(db) == 30
    set(db, 0)
    assert get(db) == 0


def test_set_rejects_out_of_set_value(db: sqlite3.Connection) -> None:
    """Out-of-set value raises ValueError and leaves the persisted row untouched."""
    set(db, 60)
    assert get(db) == 60

    with pytest.raises(ValueError):
        set(db, 5)

    assert get(db) == 60

    for invalid in [-1, 1, 15, 45, 90, 3600]:
        with pytest.raises(ValueError):
            set(db, invalid)
    assert get(db) == 60


def test_get_falls_back_to_default_on_out_of_set_stored_value(
    db: sqlite3.Connection,
) -> None:
    """Hand-edited out-of-set stored value → ``get`` returns the default, not the bad value."""
    with db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETTINGS_KEY, "45"),
        )

    assert get(db) == PLAY_CADENCE_SECONDS_DEFAULT


def test_get_falls_back_to_default_on_missing_row(db: sqlite3.Connection) -> None:
    """Absent settings row → silent fallback to the default."""
    with db:
        db.execute("DELETE FROM settings WHERE key = ?", (_SETTINGS_KEY,))

    assert get(db) == PLAY_CADENCE_SECONDS_DEFAULT
