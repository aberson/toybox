"""Phase Y Step Y4 — interests activation + scene resolver chain.

Covers ``normalize_interests`` (free text -> allowlisted tokens),
``resolve_scene_id`` (template -> interest -> default chain), and the
``ChildProfileRow``/``ResolvedChildren`` interests plumbing through
``aggregate_child_constraints``.
"""

from __future__ import annotations

import pathlib
import sqlite3
from collections.abc import Iterator

import pytest

from toybox.activities.content_resolver import (
    ChildProfileRow,
    ResolvedChildren,
    aggregate_child_constraints,
    normalize_interests,
    resolve_child_profiles,
    resolve_scene_id,
)
from toybox.activities.scene_catalog import DEFAULT_SCENE_ID
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations

# --- normalize_interests ----------------------------------------------------


def test_normalize_interests_matches_single_and_multiword() -> None:
    assert normalize_interests("loves dancing") == ("dancing",)
    # multi-word tag must match as a phrase
    assert normalize_interests("Child B loves the periodic table") == ("periodic table",)


def test_normalize_interests_is_case_insensitive() -> None:
    assert normalize_interests("DANCING and SPACE") == ("dancing", "space")


def test_normalize_interests_whole_word_only() -> None:
    # "park" must not match inside "sparkle"
    assert normalize_interests("sparkle and shimmer") == ()


def test_normalize_interests_empty_and_unknown() -> None:
    assert normalize_interests(None) == ()
    assert normalize_interests("") == ()
    assert normalize_interests("   ") == ()
    assert normalize_interests("quantum chromodynamics") == ()


def test_normalize_interests_dedupes_in_tag_order() -> None:
    # both "dancing" and "ballet" map to stage; order follows tag-declaration.
    result = normalize_interests("ballet, dancing, ballet")
    assert result == ("dancing", "ballet")


# --- resolve_scene_id -------------------------------------------------------


def test_template_scene_id_wins() -> None:
    resolved = ResolvedChildren(interests=("dancing",))
    # explicit author choice beats interest selection
    assert resolve_scene_id("castle", resolved) == "castle"


def test_interest_selects_when_no_template_scene() -> None:
    assert resolve_scene_id(None, ResolvedChildren(interests=("periodic table",))) == "lab"
    assert resolve_scene_id(None, ResolvedChildren(interests=("dancing",))) == "stage"


def test_default_when_neither() -> None:
    assert resolve_scene_id(None, ResolvedChildren()) == DEFAULT_SCENE_ID
    assert resolve_scene_id(None, ResolvedChildren(interests=())) == DEFAULT_SCENE_ID


def test_first_mapped_interest_wins() -> None:
    # owner-first order: the first token that maps to a scene is chosen
    resolved = ResolvedChildren(interests=("dancing", "periodic table"))
    assert resolve_scene_id(None, resolved) == "stage"


def test_custom_default_honored() -> None:
    assert resolve_scene_id(None, ResolvedChildren(), default="space") == "space"


# --- aggregate_child_constraints (interests union) --------------------------


def test_aggregate_unions_interests_owner_first() -> None:
    ama = ChildProfileRow(id="ama", interests=("dancing",))
    rocket = ChildProfileRow(id="rocket", interests=("periodic table",))
    agg = aggregate_child_constraints([ama, rocket])
    assert agg.interests == ("dancing", "periodic table")
    # Owner (first profile) interest wins the scene pick.
    assert resolve_scene_id(None, agg) == "stage"


def test_aggregate_dedupes_shared_interest() -> None:
    a = ChildProfileRow(id="a", interests=("space",))
    b = ChildProfileRow(id="b", interests=("space", "dancing"))
    agg = aggregate_child_constraints([a, b])
    assert agg.interests == ("space", "dancing")


def test_aggregate_empty_profiles() -> None:
    assert aggregate_child_constraints([]).interests == ()


# --- DB-backed: resolve_child_profiles reads the interests column -----------


@pytest.fixture
def conn(tmp_path: pathlib.Path) -> Iterator[sqlite3.Connection]:
    db_path = tmp_path / "toybox.db"
    c = connect(db_path)
    try:
        run_migrations(c)
        yield c
    finally:
        c.close()


def _insert_child(
    conn: sqlite3.Connection,
    *,
    child_id: str,
    interests: str | None,
    reading_level: str | None = None,
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO children "
            "(id, display_name, birthdate, pronouns, reading_level, "
            " interests, comfort, notes) "
            "VALUES (?, ?, NULL, NULL, ?, ?, NULL, NULL)",
            (child_id, f"Child {child_id}", reading_level, interests),
        )


def test_resolve_child_profiles_reads_interests(conn: sqlite3.Connection) -> None:
    _insert_child(conn, child_id="rocket", interests="loves the periodic table and space")
    out = resolve_child_profiles(conn, ["rocket"])
    assert "periodic table" in out.interests
    assert resolve_scene_id(None, out) == "lab"


def test_resolve_child_profiles_owner_first_across_children(conn: sqlite3.Connection) -> None:
    _insert_child(conn, child_id="ama", interests="dancing and lol dolls")
    _insert_child(conn, child_id="rocket", interests="periodic table")
    # Owner (first in child_ids) is Child A -> her interest wins the scene pick.
    out = resolve_child_profiles(conn, ["ama", "rocket"])
    assert out.interests[0] == "dancing"
    assert resolve_scene_id(None, out) == "stage"
    # Reversing the owner flips the pick.
    out2 = resolve_child_profiles(conn, ["rocket", "ama"])
    assert out2.interests[0] == "periodic table"
    assert resolve_scene_id(None, out2) == "lab"


def test_resolve_child_profiles_null_interests_is_empty(conn: sqlite3.Connection) -> None:
    _insert_child(conn, child_id="quiet", interests=None)
    out = resolve_child_profiles(conn, ["quiet"])
    assert out.interests == ()
    assert resolve_scene_id(None, out) == DEFAULT_SCENE_ID
