"""Unit coverage for :mod:`toybox.core.image_gen_mode`."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.core.image_gen_mode import (
    IMAGE_GEN_MODE_CARTOON,
    IMAGE_GEN_MODE_COMPOSITE,
    IMAGE_GEN_MODE_DEFAULT,
    current_image_gen_mode,
    set_image_gen_mode,
)
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.ws.envelope import Envelope
from toybox.ws.topics import Topic


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a fresh, migrated connection; close on teardown (Windows-safe)."""
    conn = connect(tmp_path / "toybox.db")
    try:
        run_migrations(conn)
        yield conn
    finally:
        conn.close()


def test_current_image_gen_mode_default_when_row_missing(
    db: sqlite3.Connection,
) -> None:
    # Arrange — a fresh DB without the seed row should still resolve.
    db.execute("DELETE FROM settings WHERE key = 'image_gen_mode'")

    # Act
    mode = current_image_gen_mode(db)

    # Assert
    assert mode == IMAGE_GEN_MODE_DEFAULT
    assert mode == IMAGE_GEN_MODE_CARTOON


@pytest.mark.parametrize(
    "mode",
    [IMAGE_GEN_MODE_CARTOON, IMAGE_GEN_MODE_COMPOSITE],
)
def test_set_image_gen_mode_round_trip(
    db: sqlite3.Connection,
    mode: str,
) -> None:
    # Act
    result = set_image_gen_mode(db, mode)

    # Assert
    assert result == mode
    assert current_image_gen_mode(db) == mode


@pytest.mark.parametrize("invalid", ["foo", ""])
def test_set_image_gen_mode_rejects_invalid(
    db: sqlite3.Connection,
    invalid: str,
) -> None:
    with pytest.raises(ValueError):
        set_image_gen_mode(db, invalid)


def test_set_image_gen_mode_rejects_none(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        set_image_gen_mode(db, None)  # type: ignore[arg-type]


def test_set_image_gen_mode_emits_envelope(db: sqlite3.Connection) -> None:
    # Arrange
    captured: list[Envelope] = []

    # Act
    set_image_gen_mode(db, IMAGE_GEN_MODE_COMPOSITE, publisher=captured.append)

    # Assert
    assert len(captured) == 1
    envelope = captured[0]
    assert envelope.topic is Topic.image_gen_mode
    assert envelope.payload == {"mode": IMAGE_GEN_MODE_COMPOSITE}
    assert envelope.schema_version == 1


def test_set_image_gen_mode_no_publisher_ok(db: sqlite3.Connection) -> None:
    # Act + Assert — None publisher must not raise.
    result = set_image_gen_mode(db, IMAGE_GEN_MODE_CARTOON, publisher=None)
    assert result == IMAGE_GEN_MODE_CARTOON
