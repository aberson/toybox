"""Unit coverage for :mod:`toybox.core.spoken_text_limit`."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.core.spoken_text_limit import (
    DEFAULT,
    SPOKEN_TEXT_LIMIT_VALID,
)
from toybox.core.spoken_text_limit import (
    get_spoken_text_limit as get,
)
from toybox.core.spoken_text_limit import (
    set_spoken_text_limit as set,
)
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a fresh, migrated connection; close on teardown (Windows-safe)."""
    conn = connect(tmp_path / "toybox.db")
    try:
        run_migrations(conn)
        yield conn
    finally:
        conn.close()


def test_get_default_when_row_missing(
    db: sqlite3.Connection,
) -> None:
    """Absent settings row -> silent fallback to the default (no log noise)."""
    # Arrange — migration 0022 seeded the row; remove it to simulate
    # legacy / hand-edited DBs that predate the seed.
    db.execute("DELETE FROM settings WHERE key = 'spoken_text_limit'")

    # Act
    value = get(db)

    # Assert
    assert value == DEFAULT


@pytest.mark.parametrize(
    ("stored_value", "check_value_in_message"),
    [
        # Unparseable non-int blob.
        ("abc", True),
        # Parseable int that's not a canonical preset.
        ("200", True),
        # Absurdly long corrupt value — log line is truncated so the raw
        # blob won't appear verbatim; we just want the warning to fire.
        ("x" * 200, False),
    ],
    ids=["unparseable", "out_of_set", "long_corrupt"],
)
def test_get_corrupt_value_logs_warning(
    db: sqlite3.Connection,
    caplog: pytest.LogCaptureFixture,
    stored_value: str,
    check_value_in_message: bool,
) -> None:
    """Corrupt or out-of-set stored value -> exactly one WARNING + fallback."""
    # Arrange — hand-edit the settings row to a value the helper can't trust.
    with db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("spoken_text_limit", stored_value),
        )

    # Act
    with caplog.at_level(logging.WARNING, logger="toybox.core.spoken_text_limit"):
        value = get(db)

    # Assert — fallback + exactly one warning on the helper's logger.
    assert value == DEFAULT
    warnings = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING
        and rec.name == "toybox.core.spoken_text_limit"
    ]
    assert len(warnings) == 1

    if check_value_in_message:
        # For short, non-truncated values: the offending value must appear
        # somewhere in the record (message or args) so we catch a regression
        # where the warning fires on the wrong value.
        record = warnings[0]
        haystack = record.getMessage() + " " + repr(record.args)
        assert stored_value in haystack


def test_set_round_trips_every_preset(
    db: sqlite3.Connection,
) -> None:
    """Every canonical preset round-trips cleanly through set -> get."""
    for preset in sorted(SPOKEN_TEXT_LIMIT_VALID):
        result = set(db, preset)
        assert result == preset
        assert get(db) == preset


def test_set_rejects_invalid_ints(
    db: sqlite3.Connection,
) -> None:
    """Out-of-set ints raise ValueError with a value-bearing message."""
    for invalid in [-1, 1, 75, 125, 200, 300]:
        with pytest.raises(ValueError, match=r"invalid spoken text limit"):
            set(db, invalid)
