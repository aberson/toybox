"""Unit coverage for the ``boss_fights_enabled`` boolean feature flag.

Phase W Step W5. Mirrors the Phase K feature-flag unit convention
(:mod:`tests.unit.core.test_phase_k_feature_flags`): fresh-migrated-DB
fixture, seeded-default assertion, set/get round trip. The non-bool
rejection guard lives in the shared
:class:`toybox.core._feature_flag.FeatureFlagSetting` helper and is already
covered there — not re-tested here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.core import boss_fights_enabled
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(tmp_path / "toybox.db")
    try:
        run_migrations(conn)
        yield conn
    finally:
        conn.close()


def test_default_constant_is_true() -> None:
    assert boss_fights_enabled.BOSS_FIGHTS_ENABLED_DEFAULT is True


def test_migration_seeds_default_true(db: sqlite3.Connection) -> None:
    """Migration 0028 seeds the row matching the module default."""
    assert boss_fights_enabled.get(db) is True


def test_set_round_trips(db: sqlite3.Connection) -> None:
    boss_fights_enabled.set(db, False)
    assert boss_fights_enabled.get(db) is False
    boss_fights_enabled.set(db, True)
    assert boss_fights_enabled.get(db) is True


def test_set_rejects_non_bool(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        boss_fights_enabled.set(db, "true")  # type: ignore[arg-type]
