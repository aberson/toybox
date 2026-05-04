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
    ResolvedChildren,
    aggregate_child_constraints,
    apply_banned_themes_filter,
    build_claude_directive,
    resolve_child_profiles,
    resolve_rooms,
    resolve_toys,
)
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
) -> None:
    """Insert a toy with sensible defaults; image_hash is unique-or-null."""
    h = image_hash if image_hash is not None else f"hash-{toy_id}"
    with conn:
        conn.execute(
            "INSERT INTO toys "
            "(id, display_name, image_path, image_hash, type, tags, persona_id, "
            " archived, created_at, last_used_at) "
            "VALUES (?, ?, ?, ?, NULL, ?, ?, ?, '2026-01-01T00:00:00Z', ?)",
            (
                toy_id,
                display_name,
                f"img/{toy_id}.png",
                h,
                tags,
                persona_id,
                archived,
                last_used_at,
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
    banned_themes: str | None = None,
    reading_level: str | None = None,
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO children "
            "(id, display_name, birthdate, pronouns, reading_level, "
            " interests, comfort, banned_themes, notes) "
            "VALUES (?, ?, NULL, NULL, ?, NULL, NULL, ?, NULL)",
            (child_id, display_name, reading_level, banned_themes),
        )


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


def test_resolve_child_profiles_empty_ids(conn: sqlite3.Connection) -> None:
    out = resolve_child_profiles(conn, [])
    assert out == ResolvedChildren()


def test_resolve_child_profiles_single_child(conn: sqlite3.Connection) -> None:
    _insert_child(
        conn, child_id="c1", banned_themes="scary, loud noises", reading_level="early-reader"
    )
    out = resolve_child_profiles(conn, ["c1"])
    assert out.banned_themes == ("loud noises", "scary")
    assert out.reading_level == "early-reader"


def test_resolve_child_profiles_unknown_id_returns_default(
    conn: sqlite3.Connection,
) -> None:
    out = resolve_child_profiles(conn, ["does-not-exist"])
    assert out == ResolvedChildren()


def test_aggregate_child_constraints_union_banned_themes() -> None:
    profiles = [
        ChildProfileRow(id="a", banned_themes=("scary", "Loud"), reading_level="early-reader"),
        ChildProfileRow(id="b", banned_themes=("loud", "spiders"), reading_level="fluent"),
    ]
    out = aggregate_child_constraints(profiles)
    # Lowercased, deduped, sorted.
    assert out.banned_themes == ("loud", "scary", "spiders")


def test_aggregate_child_constraints_minimum_reading_level() -> None:
    profiles = [
        ChildProfileRow(id="a", reading_level="fluent"),
        ChildProfileRow(id="b", reading_level="pre-reader"),
        ChildProfileRow(id="c", reading_level="early-reader"),
    ]
    out = aggregate_child_constraints(profiles)
    assert out.reading_level == "pre-reader"


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


def test_resolve_child_profiles_multi_child(conn: sqlite3.Connection) -> None:
    _insert_child(conn, child_id="a", banned_themes="scary", reading_level="pre-reader")
    _insert_child(conn, child_id="b", banned_themes="loud", reading_level="fluent")
    out = resolve_child_profiles(conn, ["a", "b"])
    assert out.banned_themes == ("loud", "scary")
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
