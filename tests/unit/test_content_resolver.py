"""Unit tests for ``toybox.activities.content_resolver``.

Step 19 wires real catalog content (toys, rooms, child profiles) into
the generator. These tests cover the resolver primitives in isolation:
the SQL queries against the schema, the deterministic sampling, the
banned-themes filter, the directive builder, and the multi-child
aggregation.

Each test uses a fresh in-memory SQLite DB seeded by hand — the
:func:`run_migrations` helper sets up the v1 schema. Because all
queries the resolver emits are read-only, in-memory works fine and is
faster than a tmp-file DB.
"""

from __future__ import annotations

import logging
import pathlib
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from toybox.activities.content_resolver import (
    DEFAULT_ROOMS_LIMIT,
    DEFAULT_TOYS_LIMIT,
    ROOMS_LIMIT_ENV,
    SAFE_DEFAULT_TEMPLATE,
    TOYS_LIMIT_ENV,
    ChildProfileRow,
    GenericDescriptor,
    ResolvedChildren,
    ResolvedToy,
    aggregate_child_constraints,
    apply_banned_themes_filter,
    build_claude_directive,
    resolve_child_profiles,
    resolve_role_slots,
    resolve_rooms,
    resolve_toys,
)
from toybox.activities.generic_descriptors import GENERIC_DESCRIPTORS
from toybox.activities.roles import Role
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: pathlib.Path) -> Iterator[sqlite3.Connection]:
    """Per-test SQLite DB with the full v1 schema applied."""
    db_path = tmp_path / "toybox.db"
    c = connect(db_path)
    try:
        run_migrations(c)
        yield c
    finally:
        c.close()


def _insert_toy(
    conn: sqlite3.Connection,
    *,
    toy_id: str,
    display_name: str,
    tags: str | None = None,
    persona_id: str | None = None,
    archived: int = 0,
    last_used_at: str | None = None,
    image_hash: str | None = None,
    active: int = 1,
) -> None:
    """Insert a toy with sensible defaults; image_hash is unique-or-null."""
    h = image_hash if image_hash is not None else f"hash-{toy_id}"
    with conn:
        conn.execute(
            "INSERT INTO toys "
            "(id, display_name, image_path, image_hash, type, tags, persona_id, "
            " archived, created_at, last_used_at, active) "
            "VALUES (?, ?, ?, ?, NULL, ?, ?, ?, '2026-01-01T00:00:00Z', ?, ?)",
            (
                toy_id,
                display_name,
                f"img/{toy_id}.png",
                h,
                tags,
                persona_id,
                archived,
                last_used_at,
                active,
            ),
        )


def _insert_room(
    conn: sqlite3.Connection,
    *,
    room_id: str,
    display_name: str | None,
    features: list[str] | None = None,
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO rooms (id, display_name, image_path, image_hash, notes) "
            "VALUES (?, ?, NULL, NULL, NULL)",
            (room_id, display_name),
        )
        for i, feature in enumerate(features or []):
            conn.execute(
                "INSERT INTO room_features (id, room_id, name, tags) VALUES (?, ?, ?, NULL)",
                (f"{room_id}-f{i}", room_id, feature),
            )


def _insert_child(
    conn: sqlite3.Connection,
    *,
    child_id: str,
    display_name: str = "Test Child",
    reading_level: str | None = None,
) -> None:
    """Insert a child row.

    Phase H Step H4: ``banned_themes`` is no longer a per-child column —
    the value is household-global. Tests that want a banned-themes
    seed should call :func:`_seed_banned_themes` separately; tests
    that check aggregation should write directly to the ``settings``
    table via that helper.
    """
    with conn:
        conn.execute(
            "INSERT INTO children "
            "(id, display_name, birthdate, pronouns, reading_level, "
            " interests, comfort, notes) "
            "VALUES (?, ?, NULL, NULL, ?, NULL, NULL, NULL)",
            (child_id, display_name, reading_level),
        )


def _seed_banned_themes(conn: sqlite3.Connection, value: str) -> None:
    """Write the household-global banned-themes string to ``settings``.

    Replaces the per-child seeding the pre-H4 tests did. Passing an
    empty string deletes the row, matching the contract of
    :func:`toybox.core.banned_themes.set_banned_themes_global`.
    """
    from toybox.core.banned_themes import set_banned_themes_global

    set_banned_themes_global(conn, value)


# ---------------------------------------------------------------------------
# resolve_toys
# ---------------------------------------------------------------------------


def test_resolve_toys_empty_table_returns_empty_list(conn: sqlite3.Connection) -> None:
    assert resolve_toys(conn) == []


def test_resolve_toys_skips_archived(conn: sqlite3.Connection) -> None:
    _insert_toy(conn, toy_id="t1", display_name="Alpha", archived=0)
    _insert_toy(conn, toy_id="t2", display_name="Bravo", archived=1)
    out = resolve_toys(conn)
    assert [t.id for t in out] == ["t1"]


def test_resolve_toys_skips_inactive(conn: sqlite3.Connection) -> None:
    """Migration 0018: ``active = 0`` rows must be excluded from the
    role-casting pool. Active toys remain; archived rows continue to be
    excluded independently of the active flag."""
    _insert_toy(conn, toy_id="t-on", display_name="On", active=1)
    _insert_toy(conn, toy_id="t-off", display_name="Off", active=0)
    _insert_toy(
        conn, toy_id="t-archived-and-off", display_name="Gone", archived=1, active=0
    )
    out = [t.id for t in resolve_toys(conn)]
    assert out == ["t-on"]


def test_resolve_toys_recency_sorted_with_id_tiebreak(conn: sqlite3.Connection) -> None:
    # Three toys: two with the same last_used_at (id-tiebreak), one newer.
    _insert_toy(conn, toy_id="t-old-b", display_name="Bravo", last_used_at="2026-01-01T00:00:00Z")
    _insert_toy(conn, toy_id="t-old-a", display_name="Alpha", last_used_at="2026-01-01T00:00:00Z")
    _insert_toy(conn, toy_id="t-new", display_name="Charlie", last_used_at="2026-02-01T00:00:00Z")
    _insert_toy(conn, toy_id="t-never", display_name="Delta", last_used_at=None)
    out = [t.id for t in resolve_toys(conn)]
    # Newest first; same timestamp tied by id ASC; null = oldest.
    assert out == ["t-new", "t-old-a", "t-old-b", "t-never"]


def test_resolve_toys_caps_at_limit(conn: sqlite3.Connection) -> None:
    for i in range(20):
        _insert_toy(
            conn,
            toy_id=f"t{i:02d}",
            display_name=f"Toy {i:02d}",
            last_used_at=f"2026-01-{i + 1:02d}T00:00:00Z",
        )
    out = resolve_toys(conn, limit=5)
    assert len(out) == 5


def test_resolve_toys_default_limit_from_env(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for i in range(10):
        _insert_toy(conn, toy_id=f"t{i:02d}", display_name=f"Toy {i:02d}")
    monkeypatch.setenv(TOYS_LIMIT_ENV, "3")
    out = resolve_toys(conn)
    assert len(out) == 3


def test_resolve_toys_default_limit_when_env_unset(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TOYS_LIMIT_ENV, raising=False)
    for i in range(DEFAULT_TOYS_LIMIT + 3):
        _insert_toy(conn, toy_id=f"t{i:02d}", display_name=f"Toy {i:02d}")
    out = resolve_toys(conn)
    assert len(out) == DEFAULT_TOYS_LIMIT


def test_resolve_toys_decodes_tags(conn: sqlite3.Connection) -> None:
    _insert_toy(conn, toy_id="t1", display_name="Alpha", tags="soft,plush, blue")
    out = resolve_toys(conn)
    assert out[0].tags == ("soft", "plush", "blue")


def test_resolve_toys_zero_limit_returns_empty(conn: sqlite3.Connection) -> None:
    _insert_toy(conn, toy_id="t1", display_name="Alpha")
    assert resolve_toys(conn, limit=0) == []


# ---------------------------------------------------------------------------
# H3 + L1: env-var malformed-fallback for both _toys_limit and _rooms_limit
# ---------------------------------------------------------------------------


def test_resolve_toys_blank_env_uses_default(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Empty/whitespace env value silently falls through to the default —
    no WARNING (the operator simply unset / set to empty)."""
    for i in range(DEFAULT_TOYS_LIMIT + 2):
        _insert_toy(conn, toy_id=f"t{i:02d}", display_name=f"Toy {i:02d}")
    monkeypatch.setenv(TOYS_LIMIT_ENV, "")
    with caplog.at_level(logging.WARNING):
        out = resolve_toys(conn)
    assert len(out) == DEFAULT_TOYS_LIMIT
    # Blank is not a malformed value; no WARNING expected.
    assert not [r for r in caplog.records if TOYS_LIMIT_ENV in r.message]


def test_resolve_toys_non_int_env_warns_and_uses_default(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-int env value emits a WARNING and falls back to the default."""
    for i in range(DEFAULT_TOYS_LIMIT + 2):
        _insert_toy(conn, toy_id=f"t{i:02d}", display_name=f"Toy {i:02d}")
    monkeypatch.setenv(TOYS_LIMIT_ENV, "abc")
    with caplog.at_level(logging.WARNING):
        out = resolve_toys(conn)
    assert len(out) == DEFAULT_TOYS_LIMIT
    warnings = [r for r in caplog.records if TOYS_LIMIT_ENV in r.message]
    assert warnings, f"expected WARNING for {TOYS_LIMIT_ENV}=abc; saw {caplog.records!r}"
    assert any("not an int" in r.message for r in warnings)


def test_resolve_toys_negative_env_warns_and_uses_default(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A negative env cap MUST fall back to the default (not silently
    disable the resolver) and MUST emit a WARNING.

    Pre-fix behaviour: ``max(0, n)`` returned 0 on n=-5, which short-
    circuited the resolver to an empty list — i.e. a typo silently
    disabled toys for every dispatch.
    """
    for i in range(DEFAULT_TOYS_LIMIT + 2):
        _insert_toy(conn, toy_id=f"t{i:02d}", display_name=f"Toy {i:02d}")
    monkeypatch.setenv(TOYS_LIMIT_ENV, "-5")
    with caplog.at_level(logging.WARNING):
        out = resolve_toys(conn)
    # Default applied — NOT 0 (the pre-fix bug).
    assert len(out) == DEFAULT_TOYS_LIMIT
    warnings = [r for r in caplog.records if TOYS_LIMIT_ENV in r.message]
    assert warnings, f"expected WARNING for {TOYS_LIMIT_ENV}=-5; saw {caplog.records!r}"
    assert any("negative" in r.message for r in warnings)


def test_resolve_rooms_blank_env_uses_default(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    for i in range(DEFAULT_ROOMS_LIMIT + 2):
        _insert_room(conn, room_id=f"r{i:02d}", display_name=f"Room {i:02d}")
    monkeypatch.setenv(ROOMS_LIMIT_ENV, "")
    with caplog.at_level(logging.WARNING):
        out = resolve_rooms(conn)
    assert len(out) == DEFAULT_ROOMS_LIMIT
    assert not [r for r in caplog.records if ROOMS_LIMIT_ENV in r.message]


def test_resolve_rooms_non_int_env_warns_and_uses_default(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    for i in range(DEFAULT_ROOMS_LIMIT + 2):
        _insert_room(conn, room_id=f"r{i:02d}", display_name=f"Room {i:02d}")
    monkeypatch.setenv(ROOMS_LIMIT_ENV, "abc")
    with caplog.at_level(logging.WARNING):
        out = resolve_rooms(conn)
    assert len(out) == DEFAULT_ROOMS_LIMIT
    warnings = [r for r in caplog.records if ROOMS_LIMIT_ENV in r.message]
    assert warnings


def test_resolve_rooms_negative_env_warns_and_uses_default(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    for i in range(DEFAULT_ROOMS_LIMIT + 2):
        _insert_room(conn, room_id=f"r{i:02d}", display_name=f"Room {i:02d}")
    monkeypatch.setenv(ROOMS_LIMIT_ENV, "-5")
    with caplog.at_level(logging.WARNING):
        out = resolve_rooms(conn)
    assert len(out) == DEFAULT_ROOMS_LIMIT
    warnings = [r for r in caplog.records if ROOMS_LIMIT_ENV in r.message]
    assert warnings
    assert any("negative" in r.message for r in warnings)


def test_resolve_toys_explicit_zero_env_disables(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``"0"`` is the operator's explicit "disable resolver" knob and
    is honoured (not warned). Distinguishing 0 (intentional) from -5
    (typo) is the contract per the L1 fix."""
    _insert_toy(conn, toy_id="t1", display_name="Alpha")
    monkeypatch.setenv(TOYS_LIMIT_ENV, "0")
    with caplog.at_level(logging.WARNING):
        out = resolve_toys(conn)
    assert out == []
    # Honoured 0 → no WARNING.
    assert not [r for r in caplog.records if TOYS_LIMIT_ENV in r.message]


# ---------------------------------------------------------------------------
# resolve_rooms
# ---------------------------------------------------------------------------


def test_resolve_rooms_empty_table_returns_empty_list(conn: sqlite3.Connection) -> None:
    assert resolve_rooms(conn) == []


def test_resolve_rooms_with_features(conn: sqlite3.Connection) -> None:
    _insert_room(conn, room_id="r1", display_name="Living Room", features=["couch", "rug"])
    _insert_room(conn, room_id="r2", display_name="Kitchen", features=["sink"])
    out = resolve_rooms(conn)
    # Sorted by display_name COLLATE NOCASE → Kitchen, Living Room.
    assert [r.display_name for r in out] == ["Kitchen", "Living Room"]
    kitchen = next(r for r in out if r.id == "r2")
    assert kitchen.features == ("sink",)
    living = next(r for r in out if r.id == "r1")
    # Features are sorted alphabetically too (NOCASE asc).
    assert living.features == ("couch", "rug")


def test_resolve_rooms_caps_at_limit(conn: sqlite3.Connection) -> None:
    for i in range(10):
        _insert_room(conn, room_id=f"r{i:02d}", display_name=f"Room {i:02d}")
    out = resolve_rooms(conn, limit=4)
    assert len(out) == 4


def test_resolve_rooms_skips_unnamed_rooms(conn: sqlite3.Connection) -> None:
    _insert_room(conn, room_id="r1", display_name=None)
    _insert_room(conn, room_id="r2", display_name="Kitchen")
    out = resolve_rooms(conn)
    assert [r.id for r in out] == ["r2"]


def test_resolve_rooms_default_limit_from_env(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for i in range(8):
        _insert_room(conn, room_id=f"r{i:02d}", display_name=f"Room {i:02d}")
    monkeypatch.setenv(ROOMS_LIMIT_ENV, "2")
    assert len(resolve_rooms(conn)) == 2


def test_resolve_rooms_default_when_env_unset(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ROOMS_LIMIT_ENV, raising=False)
    for i in range(DEFAULT_ROOMS_LIMIT + 2):
        _insert_room(conn, room_id=f"r{i:02d}", display_name=f"Room {i:02d}")
    assert len(resolve_rooms(conn)) == DEFAULT_ROOMS_LIMIT


# ---------------------------------------------------------------------------
# resolve_child_profiles + aggregate_child_constraints
# ---------------------------------------------------------------------------


def test_resolve_child_profiles_empty_ids_no_global_setting(
    conn: sqlite3.Connection,
) -> None:
    """Empty ``child_ids`` + no global setting → fully default."""
    out = resolve_child_profiles(conn, [])
    assert out == ResolvedChildren()


def test_resolve_child_profiles_empty_ids_reads_global_setting(
    conn: sqlite3.Connection,
) -> None:
    """Empty ``child_ids`` still surfaces the global banned-themes value.

    Trigger-driven activities (no explicit child_ids) still have a
    household ban list; the value applies regardless of how many
    children are in scope.
    """
    _seed_banned_themes(conn, "scary, loud noises")
    out = resolve_child_profiles(conn, [])
    # _split_csv: trimmed, lowercased, dedup, first-seen order.
    assert out.banned_themes == ("scary", "loud noises")
    assert out.reading_level is None


def test_resolve_child_profiles_single_child(conn: sqlite3.Connection) -> None:
    _seed_banned_themes(conn, "scary, loud noises")
    _insert_child(conn, child_id="c1", reading_level="early-reader")
    out = resolve_child_profiles(conn, ["c1"])
    assert out.banned_themes == ("scary", "loud noises")
    assert out.reading_level == "early-reader"


def test_resolve_child_profiles_unknown_id_no_settings_returns_default(
    conn: sqlite3.Connection,
) -> None:
    out = resolve_child_profiles(conn, ["does-not-exist"])
    assert out == ResolvedChildren()


def test_aggregate_child_constraints_minimum_reading_level() -> None:
    """``aggregate_child_constraints`` aggregates ``reading_level`` only.

    Phase H Step H4: ``banned_themes`` lives in the household-global
    setting now, so the aggregator no longer touches it.
    """
    profiles = [
        ChildProfileRow(id="a", reading_level="fluent"),
        ChildProfileRow(id="b", reading_level="pre-reader"),
        ChildProfileRow(id="c", reading_level="early-reader"),
    ]
    out = aggregate_child_constraints(profiles)
    assert out.reading_level == "pre-reader"
    # banned_themes isn't aggregated here any more — the field defaults
    # to () on the resulting ResolvedChildren.
    assert out.banned_themes == ()


def test_aggregate_child_constraints_null_reading_level_doesnt_override() -> None:
    profiles = [
        ChildProfileRow(id="a", reading_level="fluent"),
        ChildProfileRow(id="b", reading_level=None),
    ]
    out = aggregate_child_constraints(profiles)
    # ``None`` is "unknown" — doesn't drag anyone down to no-constraint.
    assert out.reading_level == "fluent"


def test_aggregate_child_constraints_all_null_levels() -> None:
    profiles = [
        ChildProfileRow(id="a", reading_level=None),
        ChildProfileRow(id="b", reading_level=None),
    ]
    out = aggregate_child_constraints(profiles)
    assert out.reading_level is None


def test_resolve_child_profiles_multi_child_reading_level_minimum(
    conn: sqlite3.Connection,
) -> None:
    """Reading level is aggregated per-child; banned_themes is global."""
    _seed_banned_themes(conn, "scary, loud")
    _insert_child(conn, child_id="a", reading_level="pre-reader")
    _insert_child(conn, child_id="b", reading_level="fluent")
    out = resolve_child_profiles(conn, ["a", "b"])
    assert out.banned_themes == ("scary", "loud")
    assert out.reading_level == "pre-reader"


def test_resolve_child_profiles_invalid_reading_level_becomes_none(
    conn: sqlite3.Connection,
) -> None:
    # Hand-insert an invalid reading_level — the column is free-form.
    _insert_child(conn, child_id="c1", reading_level="ancient-runic")
    out = resolve_child_profiles(conn, ["c1"])
    assert out.reading_level is None


# ---------------------------------------------------------------------------
# apply_banned_themes_filter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StubTemplate:
    """Fake template with the (id, title) attrs the filter inspects."""

    id: str
    title: str


def test_filter_no_banned_themes_returns_unchanged() -> None:
    templates = [_StubTemplate(id="a", title="Alpha"), _StubTemplate(id="b", title="Bravo")]
    out = apply_banned_themes_filter(templates, [])
    assert out == templates


def test_filter_drops_substring_match_in_title() -> None:
    templates = [
        _StubTemplate(id="t1", title="Scary monster hunt"),
        _StubTemplate(id="t2", title="Friendly tea party"),
    ]
    out = apply_banned_themes_filter(templates, ["scary"])
    assert [t.id for t in out] == ["t2"]  # type: ignore[attr-defined]


def test_filter_drops_substring_match_in_id() -> None:
    templates = [
        _StubTemplate(id="loud_drum_party", title="Drum party"),
        _StubTemplate(id="quiet_story", title="Quiet story"),
    ]
    out = apply_banned_themes_filter(templates, ["loud"])
    assert [t.id for t in out] == ["quiet_story"]  # type: ignore[attr-defined]


def test_filter_case_insensitive() -> None:
    templates = [_StubTemplate(id="t1", title="SCARY ZONE")]
    out = apply_banned_themes_filter(templates, ["Scary"])
    # All templates filtered → safe-default replaces them.
    assert len(out) == 1
    assert getattr(out[0], "id", "") == SAFE_DEFAULT_TEMPLATE.id


def test_filter_theme_substring_of_haystack_matches() -> None:
    # Forward direction: banned theme "scary" is a substring of the
    # template's id+title haystack "monster_party scary monster zone".
    templates = [_StubTemplate(id="monster_party", title="scary monster zone")]
    out = apply_banned_themes_filter(templates, ["scary"])
    assert len(out) == 1
    assert getattr(out[0], "id", "") == SAFE_DEFAULT_TEMPLATE.id


def test_filter_all_banned_falls_back_to_safe_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    templates = [_StubTemplate(id="t1", title="Scary"), _StubTemplate(id="t2", title="Spooky")]
    with caplog.at_level(logging.WARNING):
        out = apply_banned_themes_filter(templates, ["scary", "spooky"])
    assert len(out) == 1
    assert getattr(out[0], "id", "") == SAFE_DEFAULT_TEMPLATE.id
    assert any("safe-default" in r.message or "filtered" in r.message for r in caplog.records)


def test_filter_safe_default_override() -> None:
    # Caller can supply their own safe-default object.
    custom = _StubTemplate(id="custom_safe", title="Safe")
    templates = [_StubTemplate(id="t1", title="Scary")]
    out = apply_banned_themes_filter(templates, ["scary"], safe_default=custom)
    assert out == [custom]


def test_filter_skips_non_string_banned_entries() -> None:
    """L2: a non-string entry in ``banned_themes`` must NOT crash the
    filter — defensive isinstance check before ``.strip()``."""
    templates = [_StubTemplate(id="t1", title="Scary"), _StubTemplate(id="t2", title="Friendly")]
    # Mix of a non-string (None) and a real banned theme. The previous
    # behaviour would crash on ``None.strip()``.
    out = apply_banned_themes_filter(templates, [None, "scary"])  # type: ignore[list-item]
    assert [t.id for t in out] == ["t2"]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# build_claude_directive
# ---------------------------------------------------------------------------


def test_directive_empty_inputs_returns_empty() -> None:
    assert build_claude_directive([], None) == ""


def test_directive_banned_themes_only() -> None:
    out = build_claude_directive(["scary", "loud"], None)
    # Sorted, deduped.
    assert out == "Do NOT include any of: loud, scary."


def test_directive_pre_reader() -> None:
    out = build_claude_directive([], "pre-reader")
    assert "very simple words" in out.lower()
    assert "6 words" in out


def test_directive_early_reader() -> None:
    out = build_claude_directive([], "early-reader")
    assert "simple words" in out.lower()


def test_directive_fluent_returns_empty() -> None:
    # Fluent has no extra constraint per spec.
    assert build_claude_directive([], "fluent") == ""


def test_directive_combined() -> None:
    out = build_claude_directive(["scary"], "pre-reader")
    lines = out.splitlines()
    assert any("Do NOT include" in line for line in lines)
    assert any("very simple words" in line.lower() for line in lines)


def test_directive_skips_blank_themes() -> None:
    # Empty/whitespace-only themes shouldn't leak into the directive.
    out = build_claude_directive(["", "   ", "scary"], None)
    assert out == "Do NOT include any of: scary."


def test_directive_splits_embedded_commas() -> None:
    """L3: A single banned-theme entry containing a comma must render
    as TWO themes — the resolver pipeline normally splits on ``,``
    upstream, but the helper is exported and a third-party caller might
    pass a single ``"scary, loud"`` entry by mistake. The previous
    behaviour rendered ``"Do NOT include any of: scary, loud."`` which
    Claude reads as two themes anyway, but it left ``"scary, loud"`` as
    a SINGLE banned-theme name in the de-dup set, which silently broke
    the dedup contract.
    """
    out = build_claude_directive(["scary, loud"], None)
    # Both themes are present and lowercased + sorted.
    assert out == "Do NOT include any of: loud, scary."


def test_directive_ignores_non_string_entries() -> None:
    """Defensive: a non-string entry (e.g. None) MUST be silently
    skipped rather than crashing on ``.strip()``."""
    out = build_claude_directive(["scary", None, 42, "loud"], None)  # type: ignore[list-item]
    assert out == "Do NOT include any of: loud, scary."


# ---------------------------------------------------------------------------
# Phase K Step K4 — resolve_role_slots
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StubPersona:
    """Minimal :class:`_PersonaLike` stub for K4 tests.

    Plain frozen dataclass — the picker's :class:`_PersonaLike` protocol
    only requires the two attributes (``id`` + ``role_weights``), so the
    test gets to avoid pulling in the SQLite-backed loader / Pydantic
    ``RoleWeights`` machinery.
    """

    id: str
    role_weights: dict[str, float]


@dataclass(frozen=True)
class _StubTemplateWithRoles:
    """Minimal template-shape stub for K4 tests.

    The picker reads ``id`` + ``required_roles`` + ``optional_roles``
    via ``getattr``, so a small dataclass is enough — keeps the test
    free of the full Pydantic ``Template`` model and its 15+ required
    fields.
    """

    id: str
    required_roles: tuple[Role, ...] = ()
    optional_roles: tuple[Role, ...] = ()


def _toy(toy_id: str, display_name: str | None = None) -> ResolvedToy:
    return ResolvedToy(id=toy_id, display_name=display_name or toy_id.upper())


def test_resolve_role_slots_role_enum_is_identity_locked() -> None:
    """code-quality.md §2: tests use ``is`` on at least one Role member
    to lock the single-source-of-truth import (no shadow re-declaration).

    Catches a future regression that re-defines ``Role`` locally under
    ``content_resolver``; the new local enum would compare ``==`` but
    NOT ``is`` against the canonical one.
    """
    from toybox.activities.content_resolver import Role as ImportedRole

    assert ImportedRole.quest_giver is Role.quest_giver
    assert ImportedRole is Role


def test_resolve_role_slots_empty_template_returns_empty_dict() -> None:
    """A template with neither required nor optional roles produces
    an empty dict — backward-compat with the 200 shipped branching
    templates that omit role declarations entirely."""
    tpl = _StubTemplateWithRoles(id="t1")
    persona = _StubPersona(id="p1", role_weights={})
    out = resolve_role_slots(tpl, [_toy("a")], persona, seed=42)
    assert out == {}


def test_resolve_role_slots_required_only_assigns_all() -> None:
    """Two required roles + two toys → every role filled with a real
    toy, no GenericDescriptor falls back. ``quest_giver`` and
    ``guide_mentor`` are valid :class:`Role` members."""
    tpl = _StubTemplateWithRoles(
        id="t1",
        required_roles=(Role.quest_giver, Role.guide_mentor),
    )
    persona = _StubPersona(id="p1", role_weights={})
    out = resolve_role_slots(tpl, [_toy("a"), _toy("b")], persona, seed=42)
    assert out is not None
    assert set(out.keys()) == {Role.quest_giver.value, Role.guide_mentor.value}
    for v in out.values():
        assert isinstance(v, ResolvedToy)
    # Distinct-toy invariant: no toy fills two roles.
    picked_ids = {v.id for v in out.values() if isinstance(v, ResolvedToy)}
    assert len(picked_ids) == 2


def test_resolve_role_slots_determinism_same_inputs_same_output() -> None:
    """Same ``(template_id, sorted(toy_ids), persona_id, seed)`` →
    byte-identical output across repeated calls."""
    tpl = _StubTemplateWithRoles(
        id="t1",
        required_roles=(Role.quest_giver,),
        optional_roles=(Role.guide_mentor, Role.sidekick),
    )
    persona = _StubPersona(id="wiz", role_weights={"quest_giver": 1.5})
    toys = [_toy("alpha"), _toy("bravo"), _toy("charlie")]

    first = resolve_role_slots(tpl, toys, persona, seed=42)
    second = resolve_role_slots(tpl, toys, persona, seed=42)
    third = resolve_role_slots(tpl, list(reversed(toys)), persona, seed=42)

    assert first == second
    # Caller-order on ``available_toys`` is NOT part of the determinism
    # key; the picker sorts internally.
    assert first == third


def test_resolve_role_slots_determinism_persona_id_matters() -> None:
    """Two personas with identical ``role_weights`` but distinct ids
    produce distinct casts at the same seed — persona_id is part of
    the determinism fingerprint per the K4 spec."""
    tpl = _StubTemplateWithRoles(
        id="t1",
        required_roles=(Role.quest_giver,),
        optional_roles=(Role.guide_mentor,),
    )
    pa = _StubPersona(id="alice", role_weights={})
    pb = _StubPersona(id="bob", role_weights={})
    toys = [_toy("t01"), _toy("t02"), _toy("t03"), _toy("t04")]

    out_a = resolve_role_slots(tpl, toys, pa, seed=42)
    out_b = resolve_role_slots(tpl, toys, pb, seed=42)

    # Same shape (same role keys) but at least one role's pick differs
    # — guaranteed by the deterministic seed mixing persona_id.
    assert out_a is not None
    assert out_b is not None
    assert out_a.keys() == out_b.keys()
    assert out_a != out_b


def test_resolve_role_slots_persona_weight_biases_picks_across_seeds() -> None:
    """Persona with ``role_weights={"quest_giver": 10.0}`` biases the
    pick toward the sorted-first toy across an explicit list of seeds.

    The K4 spec says the persona's role-weights "bias the normalized
    distribution". With a heavy weight (10.0) the picker should
    pick the sorted-first toy id more often than the other across an
    enumerated seed range — no actual RNG sampling in the test, just
    a deterministic enumeration.
    """
    tpl = _StubTemplateWithRoles(
        id="t1",
        required_roles=(Role.quest_giver,),
    )
    biased = _StubPersona(id="biased", role_weights={"quest_giver": 10.0})
    toys = [_toy("aa"), _toy("zz")]  # Sorted-first id is "aa".

    seeds = list(range(50))
    aa_wins = 0
    zz_wins = 0
    for s in seeds:
        out = resolve_role_slots(tpl, toys, biased, seed=s)
        assert out is not None
        picked = out[Role.quest_giver.value]
        assert isinstance(picked, ResolvedToy)
        if picked.id == "aa":
            aa_wins += 1
        else:
            zz_wins += 1
    # With weight 10.0 vs 1.0, the sorted-first ("aa") candidate gets
    # ~10/11 of the probability mass. Across 50 deterministic seeds
    # the bias is dominant — pin a strict majority threshold.
    assert aa_wins > zz_wins
    assert aa_wins >= 35, (
        f"expected dominant bias for sorted-first toy, got aa={aa_wins} zz={zz_wins}"
    )


def test_resolve_role_slots_uniform_when_no_weights() -> None:
    """A persona with empty ``role_weights`` falls back to uniform.

    The deterministic seed still produces a stable answer per seed,
    but across enumerated seeds neither candidate dominates by 4:1.
    """
    tpl = _StubTemplateWithRoles(
        id="t1",
        required_roles=(Role.quest_giver,),
    )
    neutral = _StubPersona(id="neutral", role_weights={})
    toys = [_toy("aa"), _toy("zz")]
    aa_wins = 0
    for s in range(50):
        out = resolve_role_slots(tpl, toys, neutral, seed=s)
        assert out is not None
        picked = out[Role.quest_giver.value]
        assert isinstance(picked, ResolvedToy)
        if picked.id == "aa":
            aa_wins += 1
    zz_wins = 50 - aa_wins
    # Roughly 50/50 — neither side has overwhelming dominance.
    assert 10 <= aa_wins <= 40, f"expected ~uniform split, got aa={aa_wins} zz={zz_wins}"


def test_resolve_role_slots_required_roles_exceeds_pool_returns_none() -> None:
    """Eligibility filter: more required roles than toys → ``None``.

    Mirrors :func:`toybox.activities.generator._pick_toy_entry`'s
    "return None on empty pool" precedent; the codebase does not have
    a dedicated eligibility-exception class for this case.
    """
    tpl = _StubTemplateWithRoles(
        id="t1",
        required_roles=(Role.quest_giver, Role.guide_mentor, Role.sidekick),
    )
    persona = _StubPersona(id="p1", role_weights={})
    out = resolve_role_slots(tpl, [_toy("only_one")], persona, seed=0)
    assert out is None


def test_resolve_role_slots_required_roles_equal_pool_works() -> None:
    """Edge case of the eligibility gate: required-count == toy-count
    is eligible (every required role gets exactly one toy, no slack
    for optional roles)."""
    tpl = _StubTemplateWithRoles(
        id="t1",
        required_roles=(Role.quest_giver, Role.guide_mentor),
        optional_roles=(Role.sidekick,),
    )
    persona = _StubPersona(id="p1", role_weights={})
    toys = [_toy("a"), _toy("b")]
    out = resolve_role_slots(tpl, toys, persona, seed=0)
    assert out is not None
    # Required roles filled by toys.
    assert isinstance(out[Role.quest_giver.value], ResolvedToy)
    assert isinstance(out[Role.guide_mentor.value], ResolvedToy)
    # Optional role falls back to GENERIC_DESCRIPTORS (no toys left).
    assert isinstance(out[Role.sidekick.value], GenericDescriptor)


def test_resolve_role_slots_optional_roles_fall_back_to_generic_descriptors() -> None:
    """When optional roles outnumber the leftover toy pool, the
    overflow falls back to :data:`GENERIC_DESCRIPTORS`."""
    tpl = _StubTemplateWithRoles(
        id="t1",
        required_roles=(Role.quest_giver,),
        optional_roles=(Role.guide_mentor, Role.sidekick),
    )
    persona = _StubPersona(id="p1", role_weights={})
    # Only one toy → required quest_giver gets it; both optionals fall back.
    out = resolve_role_slots(tpl, [_toy("only")], persona, seed=0)
    assert out is not None
    assert isinstance(out[Role.quest_giver.value], ResolvedToy)
    assert out[Role.quest_giver.value].id == "only"  # type: ignore[union-attr]
    guide = out[Role.guide_mentor.value]
    side = out[Role.sidekick.value]
    assert isinstance(guide, GenericDescriptor)
    assert isinstance(side, GenericDescriptor)
    # The descriptor strings come from GENERIC_DESCRIPTORS verbatim —
    # single-source-of-truth (no re-declared role names in the picker).
    assert guide.display_name == GENERIC_DESCRIPTORS["guide_mentor"]
    assert side.display_name == GENERIC_DESCRIPTORS["sidekick"]


def test_resolve_role_slots_tie_break_alphabetical_on_toy_id() -> None:
    """When the weighted draw collapses to a deterministic pick, the
    tie-break is the sorted-first toy id.

    With a single role and a neutral persona, the seed=0 draw on a
    sorted pool of {bbb, aaa, ccc} → "aaa" wins (sorted-first under
    enough seeds to anchor the contract).
    """
    tpl = _StubTemplateWithRoles(
        id="t1",
        required_roles=(Role.quest_giver,),
    )
    # Heavy persona weight collapses the distribution onto the
    # sorted-first id deterministically.
    biased = _StubPersona(id="biased", role_weights={"quest_giver": 100.0})
    toys = [_toy("zzz"), _toy("aaa"), _toy("mmm")]  # Out-of-order input.
    out = resolve_role_slots(tpl, toys, biased, seed=0)
    assert out is not None
    picked = out[Role.quest_giver.value]
    assert isinstance(picked, ResolvedToy)
    # Sorted-first id under ASC order is "aaa" — picker normalises
    # the input order before sampling.
    assert picked.id == "aaa"


def test_resolve_role_slots_no_toy_used_twice() -> None:
    """A single cast must not assign the same toy to two roles —
    mirrors the K3 distinct-toy-ceiling intent at runtime."""
    tpl = _StubTemplateWithRoles(
        id="t1",
        required_roles=(Role.quest_giver, Role.guide_mentor, Role.sidekick),
    )
    persona = _StubPersona(id="p1", role_weights={})
    toys = [_toy("a"), _toy("b"), _toy("c")]
    out = resolve_role_slots(tpl, toys, persona, seed=7)
    assert out is not None
    ids = [v.id for v in out.values() if isinstance(v, ResolvedToy)]
    assert len(ids) == 3
    assert len(set(ids)) == 3  # all distinct


def test_resolve_role_slots_unknown_role_name_in_weights_ignored() -> None:
    """A persona with a stale / typo'd role-weights key (not a member
    of :class:`Role`) must NOT crash the picker — it falls through to
    the uniform branch for the affected roles."""
    tpl = _StubTemplateWithRoles(
        id="t1",
        required_roles=(Role.quest_giver,),
    )
    # "wizard_helper" is not a Role member; the picker silently
    # ignores unknown keys.
    persona = _StubPersona(
        id="p1",
        role_weights={"wizard_helper": 10.0, "quest_giver": 1.0},
    )
    out = resolve_role_slots(tpl, [_toy("only")], persona, seed=0)
    assert out is not None
    assert out[Role.quest_giver.value].id == "only"  # type: ignore[union-attr]


def test_resolve_role_slots_generic_descriptor_has_no_toy_id() -> None:
    """The :class:`GenericDescriptor` discriminator: it has no ``id``
    attribute, so the caller's :class:`ResolvedToy` / descriptor union
    pattern-match on ``hasattr(v, 'id')`` (or ``isinstance(v,
    ResolvedToy)``) sees them distinctly."""
    descriptor = GenericDescriptor(display_name="a friendly stranger")
    assert not hasattr(descriptor, "id")
    assert descriptor.kind == "generic_descriptor"
    # ``display_name`` is the substitution-time field, mirroring the
    # ResolvedToy.display_name shape.
    assert descriptor.display_name == "a friendly stranger"


# ---------------------------------------------------------------------------
# Per-toy role restrictions (migration 0017)
# ---------------------------------------------------------------------------


def _restricted_toy(toy_id: str, allowed: tuple[str, ...]) -> ResolvedToy:
    """Build a :class:`ResolvedToy` with a specific ``allowed_roles``."""
    return ResolvedToy(
        id=toy_id,
        display_name=toy_id.upper(),
        allowed_roles=allowed,
    )


def test_role_filter_excludes_restricted_toy_from_non_allowed_role() -> None:
    """A toy restricted to ``big_bad_boss`` only must NOT be cast into
    a ``friend`` slot when another (unrestricted) toy is available.

    Setup: 2 toys — Bowser (restricted to big_bad_boss) + Owl (unrestricted).
    Template: required role = ``friend``. Picker MUST pick Owl.
    """
    tpl = _StubTemplateWithRoles(
        id="t_friend",
        required_roles=(Role.friend,),
    )
    persona = _StubPersona(id="p1", role_weights={})
    bowser = _restricted_toy("a_bowser", ("big_bad_boss",))
    owl = _toy("b_owl")
    out = resolve_role_slots(tpl, [bowser, owl], persona, seed=42)
    assert out is not None
    friend_pick = out[Role.friend.value]
    assert isinstance(friend_pick, ResolvedToy)
    assert friend_pick.id == "b_owl", (
        f"role restriction should have excluded Bowser; got {friend_pick.id!r}"
    )


def test_role_filter_picks_restricted_toy_for_allowed_role() -> None:
    """A toy restricted to a specific role IS picked for that role
    when it's in the filtered pool.

    Setup: 2 toys — Bowser (restricted to big_bad_boss) + Owl
    (unrestricted). Template: required ``big_bad_boss``. Both are
    eligible; persona weights bias toward Bowser via the id-sorted
    primary-mass branch (Bowser sorts first).
    """
    tpl = _StubTemplateWithRoles(
        id="t_boss",
        required_roles=(Role.big_bad_boss,),
    )
    # Heavy weight on big_bad_boss biases the first-sorted candidate;
    # both Bowser ("a_bowser") and Owl ("b_owl") are eligible so the
    # filter doesn't shrink the pool, but the persona weight pushes
    # toward "a_bowser" because it sorts first.
    persona = _StubPersona(id="p1", role_weights={"big_bad_boss": 100.0})
    bowser = _restricted_toy("a_bowser", ("big_bad_boss",))
    owl = _toy("b_owl")
    out = resolve_role_slots(tpl, [bowser, owl], persona, seed=42)
    assert out is not None
    pick = out[Role.big_bad_boss.value]
    assert isinstance(pick, ResolvedToy)
    assert pick.id == "a_bowser", (
        f"restricted-but-eligible Bowser should be biased into the slot; got {pick.id!r}"
    )


def test_role_filter_soft_fallback_when_filtered_pool_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When every toy's ``allowed_roles`` excludes the target role,
    the picker MUST fall back to the unfiltered pool and log it.

    The filter expresses a preference; it must never starve a slot.
    """
    tpl = _StubTemplateWithRoles(
        id="t_friend",
        required_roles=(Role.friend,),
    )
    persona = _StubPersona(id="p1", role_weights={})
    # Both toys restricted away from ``friend``; required_roles = (friend,)
    # so the picker has no way to honour the restriction. Soft fallback:
    # pick anyway, log the info-level reason.
    only_a = _restricted_toy("a", ("big_bad_boss",))
    only_b = _restricted_toy("b", ("quest_giver",))
    with caplog.at_level("INFO", logger="toybox.activities.content_resolver"):
        out = resolve_role_slots(tpl, [only_a, only_b], persona, seed=42)
    assert out is not None, "soft fallback must NOT return None when pool is non-empty"
    pick = out[Role.friend.value]
    assert isinstance(pick, ResolvedToy)
    assert pick.id in {"a", "b"}, (
        "soft fallback must pick from the unfiltered pool when filtered is empty"
    )
    # Assert the info-level log message fires once.
    fallback_msgs = [
        rec for rec in caplog.records if "had no candidates for role" in rec.getMessage()
    ]
    assert len(fallback_msgs) >= 1, (
        f"expected at least one info-level fallback log; got "
        f"{[rec.getMessage() for rec in caplog.records]!r}"
    )
    assert "friend" in fallback_msgs[0].getMessage()


def test_role_filter_unrestricted_toy_eligible_everywhere() -> None:
    """A toy with empty ``allowed_roles`` (default) is eligible for
    every role — backwards compatible with every existing catalog row.
    """
    tpl = _StubTemplateWithRoles(
        id="t_multi",
        required_roles=(Role.friend, Role.quest_giver),
    )
    persona = _StubPersona(id="p1", role_weights={})
    # Both toys unrestricted (empty tuple) — the filter is a no-op and
    # both required roles MUST be filled by a real toy.
    toys = [_toy("alpha"), _toy("bravo")]
    out = resolve_role_slots(tpl, toys, persona, seed=42)
    assert out is not None
    assert isinstance(out[Role.friend.value], ResolvedToy)
    assert isinstance(out[Role.quest_giver.value], ResolvedToy)
