"""Unit coverage for the surviving Phase K boolean feature-flag modules.

Originally eight flags; Phase L Step L5 removed the three Phase K
play-surface flags (``play_embedded_enabled``, ``play_endings_enabled``,
``play_spontaneity_enabled``) when jokes/songs migrated to per-activity
reward types. The five remaining flags share the
:mod:`toybox.core._feature_flag` storage contract; tests below are
parameterized over those five so any future drift surfaces uniformly.

Follows the feature-flag unit test convention — same fresh-migrated-DB
fixture, same defensive-fallback assertions, same set-rejection
assertions.
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


# Canonical fixture list — one row per surviving Phase K feature flag.
# All five default to True after the L5 removal of the three Phase K
# play-surface flags.
FLAGS: list[FlagFixture] = [
    FlagFixture(jokes_enabled, "jokes_enabled", True),
    FlagFixture(songs_enabled, "songs_enabled", True),
    FlagFixture(play_standalone_enabled, "play_standalone_enabled", True),
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

    Migration 0015 seeds the row; this asserts the seed value matches
    the per-module default constant. A drift between the SQL seed and
    the Python default would surface here.
    """
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


def test_migration_seeds_match_defaults(db: sqlite3.Connection) -> None:
    """Sanity guard: every still-active Phase K flag has a seed row
    matching its default.

    Complements the per-flag ``test_get_returns_seeded_default`` by
    reading the raw stored strings rather than going through the
    helper, so a hypothetical "helper returns default even when row
    is wrong" bug can't mask a seed drift.
    """
    for flag in FLAGS:
        row = db.execute("SELECT value FROM settings WHERE key = ?", (flag.key,)).fetchone()
        assert row is not None, f"migration 0015 must seed {flag.key}"
        raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
        expected = "true" if flag.default else "false"
        assert raw == expected, f"settings.{flag.key} seeded {raw!r}, expected {expected!r}"


def test_all_surviving_flags_default_true() -> None:
    """After Phase L Step L5 the lone opt-in flag (``play_spontaneity_enabled``)
    was deleted; every remaining flag defaults to ``True``.

    Code-quality §2 (one source of truth): if any future PR flips a
    default this assertion fails loudly, prompting the author to
    update the §5 defaults table alongside the module change.
    """
    assert all(f.default is True for f in FLAGS)
