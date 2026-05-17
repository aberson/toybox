"""Unit coverage for the eight Phase K boolean feature-flag modules.

Tests are parameterized over all eight per-setting modules so the
shared :mod:`toybox.core._feature_flag` storage contract is exercised
identically for each flag. Per-flag specifics (key name + default)
come from the per-setting module's ``_SETTING`` instance. The
intentional defaults divergence — seven default ``True``,
``play_spontaneity_enabled`` defaults ``False`` — is its own
parameterized test below.

Mirrors :mod:`tests.unit.core.test_play_cadence_seconds` for shape —
same fresh-migrated-DB fixture, same defensive-fallback assertions,
same set-rejection assertions.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

from toybox.core import (
    clickable_words_enabled,
    jokes_enabled,
    play_embedded_enabled,
    play_endings_enabled,
    play_spontaneity_enabled,
    play_standalone_enabled,
    read_me_button_enabled,
    songs_enabled,
)
from toybox.core._feature_flag import FeatureFlagSetting
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations


@dataclass(frozen=True)
class FlagFixture:
    """The per-flag bundle parameterizing every test in this module."""

    module: ModuleType
    key: str
    default: bool


# Canonical fixture list — one row per Phase K feature flag. The
# ``default`` column is the spec'd default from
# ``documentation/phase-k-plan.md §5`` (seven ``True`` + one ``False``).
FLAGS: list[FlagFixture] = [
    FlagFixture(jokes_enabled, "jokes_enabled", True),
    FlagFixture(songs_enabled, "songs_enabled", True),
    FlagFixture(play_standalone_enabled, "play_standalone_enabled", True),
    FlagFixture(play_embedded_enabled, "play_embedded_enabled", True),
    FlagFixture(play_endings_enabled, "play_endings_enabled", True),
    FlagFixture(play_spontaneity_enabled, "play_spontaneity_enabled", False),
    FlagFixture(clickable_words_enabled, "clickable_words_enabled", True),
    FlagFixture(read_me_button_enabled, "read_me_button_enabled", True),
]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a fresh, migrated connection; close on teardown (Windows-safe)."""
    conn = connect(tmp_path / "toybox.db")
    try:
        run_migrations(conn)
        yield conn
    finally:
        conn.close()


@pytest.mark.parametrize("flag", FLAGS, ids=[f.key for f in FLAGS])
def test_default_matches_spec(flag: FlagFixture) -> None:
    """Each per-setting module exports the spec'd default constant.

    Locks in the §5 defaults table at the module-attribute level so a
    silent flip (e.g. someone editing
    ``play_spontaneity_enabled.PLAY_SPONTANEITY_ENABLED_DEFAULT`` to
    ``True``) fails this test rather than silently changing kid-
    visible behavior.
    """
    const_name = f"{flag.key.upper()}_DEFAULT"
    assert getattr(flag.module, const_name) is flag.default


@pytest.mark.parametrize("flag", FLAGS, ids=[f.key for f in FLAGS])
def test_get_returns_seeded_default(db: sqlite3.Connection, flag: FlagFixture) -> None:
    """Migrated DB → ``get`` returns the seeded default for each flag.

    Migration 0015 seeds all eight rows; this asserts the seed values
    match the per-module default constants. A drift between the SQL
    seed and the Python default would surface here.
    """
    # Phase L L1: migration 0021 deletes the three play-surface flag rows
    # (see _PHASE_L_DROPPED_FLAG_KEYS below). Without this skip the test
    # silently asserts on the row-absent fallback path rather than the
    # seeded-value path it documents. The per-flag module + FLAGS row
    # will be removed in L5 along with this branch.
    if flag.key in _PHASE_L_DROPPED_FLAG_KEYS:
        pytest.skip(
            f"{flag.key} row deleted by migration 0021 (Phase L L1); "
            f"per-flag module will be removed in L5."
        )
    assert flag.module.get(db) is flag.default


@pytest.mark.parametrize("flag", FLAGS, ids=[f.key for f in FLAGS])
def test_set_true_then_false_round_trips(db: sqlite3.Connection, flag: FlagFixture) -> None:
    """Both bool values round-trip cleanly through set → get."""
    flag.module.set(db, True)
    assert flag.module.get(db) is True

    flag.module.set(db, False)
    assert flag.module.get(db) is False

    flag.module.set(db, True)
    assert flag.module.get(db) is True


# --- Helper-level tests ----------------------------------------------
# The non-bool-rejection guard, corrupt-stored-value fallback, and
# missing-row fallback all live in the shared
# :class:`toybox.core._feature_flag.FeatureFlagSetting` helper — the
# per-setting modules are thin wrappers around one bound instance each.
# Re-testing the same guard 8x once per flag was iter-1 spam (M2 from
# the review); the per-flag round-trip test above is the right level
# of per-flag smoke. The three tests below cover the shared guard at
# the helper level once, with parameterization over the corruption
# shapes rather than the flags. The same FAILURE class (someone breaks
# the helper guard) surfaces here in one assertion, not eight.


@pytest.fixture
def helper_flag(db: sqlite3.Connection) -> FeatureFlagSetting:
    """One ``FeatureFlagSetting`` instance used by the helper-level tests.

    Bound to the canonical ``jokes_enabled`` key — picked because it's
    the first flag in :data:`FLAGS` and has the default-true case
    (more representative; the default-false case is locked in by
    :func:`test_spontaneity_is_the_only_opt_in` below + the per-flag
    round-trip + the integration tests).
    """
    return FeatureFlagSetting(key="jokes_enabled", default=True)


@pytest.mark.parametrize(
    "invalid",
    [1, 0, "true", "false", "", None, 1.0],
    ids=["int_1", "int_0", "str_true", "str_false", "empty_str", "none", "float_1.0"],
)
def test_helper_set_rejects_non_bool(
    db: sqlite3.Connection,
    helper_flag: FeatureFlagSetting,
    invalid: object,
) -> None:
    """:meth:`FeatureFlagSetting.set` raises ValueError on every non-bool.

    Defends against a future caller passing ``1`` / ``0`` / ``"true"``
    through Python's truthy coercion — the API layer's Pydantic model
    enforces the type up front, and the core helper has its own
    isinstance guard so a direct-callsite bug surfaces immediately. The
    guard lives in one place; testing it 8x per flag was iter-1 spam.
    """
    helper_flag.set(db, True)
    assert helper_flag.get(db) is True
    with pytest.raises(ValueError):
        helper_flag.set(db, invalid)  # type: ignore[arg-type]
    # The pre-existing value is untouched.
    assert helper_flag.get(db) is True


@pytest.mark.parametrize(
    "corrupt_value",
    ["not_a_bool", "True", "False", "1", "0", "yes", "no", ""],
    ids=[
        "garbled",
        "capitalized_true",
        "capitalized_false",
        "int_1_str",
        "int_0_str",
        "yes_str",
        "no_str",
        "empty",
    ],
)
def test_helper_get_falls_back_on_corrupt_stored_value(
    db: sqlite3.Connection,
    helper_flag: FeatureFlagSetting,
    corrupt_value: str,
) -> None:
    """Out-of-set persisted strings fall back to the helper's default.

    Covers the "hand-edited DB row" / "older migration left a stale
    value" path. Re-tested once per corruption shape, not once per
    flag — the fallback lives in the helper.
    """
    with db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (helper_flag.key, corrupt_value),
        )
    assert helper_flag.get(db) is helper_flag.default


def test_helper_get_falls_back_on_missing_row(
    db: sqlite3.Connection,
    helper_flag: FeatureFlagSetting,
) -> None:
    """Absent settings row → ``get`` returns the helper's default.

    Covers a legacy DB that predates migration 0015, or a deleted seed
    row. One helper-level test rather than eight per-flag clones.
    """
    with db:
        db.execute("DELETE FROM settings WHERE key = ?", (helper_flag.key,))
    assert helper_flag.get(db) is helper_flag.default


# Phase L L1: migration 0021 deletes three Phase K play-surface flag
# rows (``play_embedded_enabled``, ``play_endings_enabled``,
# ``play_spontaneity_enabled``) as the first step of re-framing
# jokes/songs as per-activity reward types. The per-setting helper
# modules and ``FLAGS`` list are L5's territory to remove; until then
# the "seeded value" guard below excludes the three deprecated keys.
# Their absence is verified end-to-end by
# ``tests/integration/migrations/test_0019_0020_0021_phase_l_foundation.py``.
_PHASE_L_DROPPED_FLAG_KEYS: frozenset[str] = frozenset(
    {
        "play_embedded_enabled",
        "play_endings_enabled",
        "play_spontaneity_enabled",
    }
)


def test_migration_seeds_match_defaults(db: sqlite3.Connection) -> None:
    """Sanity guard: every still-active Phase K flag has a seed row
    matching its default.

    Complements the per-flag ``test_get_returns_seeded_default`` by
    reading the raw stored strings rather than going through the
    helper, so a hypothetical "helper returns default even when row
    is wrong" bug can't mask a seed drift.

    Phase L L1: the three deprecated play-surface keys in
    :data:`_PHASE_L_DROPPED_FLAG_KEYS` are skipped here — migration
    0021 deletes those rows. The per-flag ``get`` fallback path covers
    that case (returns the helper default when the row is absent).
    """
    for flag in FLAGS:
        if flag.key in _PHASE_L_DROPPED_FLAG_KEYS:
            continue
        row = db.execute("SELECT value FROM settings WHERE key = ?", (flag.key,)).fetchone()
        assert row is not None, f"migration 0015 must seed {flag.key}"
        raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
        expected = "true" if flag.default else "false"
        assert raw == expected, f"settings.{flag.key} seeded {raw!r}, expected {expected!r}"


def test_spontaneity_is_the_only_opt_in() -> None:
    """Lock the §5 table: exactly one of the eight defaults is False.

    Code-quality §2 (one source of truth): if any future PR flips the
    default of one of the seven opt-out flags, or removes
    play_spontaneity_enabled's opt-in semantics, this assertion fails
    loudly.
    """
    off_count = sum(1 for f in FLAGS if f.default is False)
    assert off_count == 1
    (off_flag,) = [f for f in FLAGS if f.default is False]
    assert off_flag.key == "play_spontaneity_enabled"
