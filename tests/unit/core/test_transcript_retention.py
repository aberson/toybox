"""Unit coverage for :mod:`toybox.core.transcript_retention`."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.core.transcript_retention import (
    RETENTION_SECONDS_DEFAULT,
    RETENTION_SECONDS_VALID,
    current_retention_seconds,
    set_retention_seconds,
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


def test_current_retention_seconds_default_when_row_missing(
    db: sqlite3.Connection,
) -> None:
    """Absent settings row → silent fallback to the default (no log noise)."""
    # Arrange — migration 0010 seeded the row; remove it to simulate
    # legacy / hand-edited DBs that predate the seed.
    db.execute("DELETE FROM settings WHERE key = 'transcript_retention_seconds'")

    # Act
    seconds = current_retention_seconds(db)

    # Assert
    assert seconds == RETENTION_SECONDS_DEFAULT


@pytest.mark.parametrize(
    ("stored_value", "check_value_in_message"),
    [
        # Unparseable non-int blob.
        ("abc", True),
        # Parseable int that's not a canonical preset.
        ("120", True),
        # Absurdly long corrupt value — log line is truncated so the raw
        # blob won't appear verbatim; we just want the warning to fire.
        ("x" * 200, False),
    ],
    ids=["unparseable", "out_of_set", "long_corrupt"],
)
def test_current_retention_seconds_corrupt_value_logs_warning(
    db: sqlite3.Connection,
    caplog: pytest.LogCaptureFixture,
    stored_value: str,
    check_value_in_message: bool,
) -> None:
    """Corrupt or out-of-set stored value → exactly one WARNING + fallback."""
    # Arrange — hand-edit the settings row to a value the helper can't trust.
    with db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("transcript_retention_seconds", stored_value),
        )

    # Act
    with caplog.at_level(logging.WARNING, logger="toybox.core.transcript_retention"):
        seconds = current_retention_seconds(db)

    # Assert — fallback + exactly one warning on the helper's logger.
    assert seconds == RETENTION_SECONDS_DEFAULT
    warnings = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING
        and rec.name == "toybox.core.transcript_retention"
    ]
    assert len(warnings) == 1

    if check_value_in_message:
        # For short, non-truncated values: the offending value must appear
        # somewhere in the record (message or args) so we catch a regression
        # where the warning fires on the wrong value.
        record = warnings[0]
        haystack = record.getMessage() + " " + repr(record.args)
        assert stored_value in haystack


def test_set_retention_seconds_round_trips_every_preset(
    db: sqlite3.Connection,
) -> None:
    """Every canonical preset round-trips cleanly through set → get."""
    for preset in sorted(RETENTION_SECONDS_VALID):
        result = set_retention_seconds(db, preset)
        assert result == preset
        assert current_retention_seconds(db) == preset


def test_set_retention_seconds_rejects_invalid_ints(
    db: sqlite3.Connection,
) -> None:
    """Out-of-set ints raise ValueError with a value-bearing message."""
    for invalid in [0, -60, 120, 86400, 1]:
        with pytest.raises(ValueError, match=r"invalid retention seconds"):
            set_retention_seconds(db, invalid)
