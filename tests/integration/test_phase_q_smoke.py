"""Phase Q Step Q6 — end-to-end smoke gate for the element-aware reward chain.

Loads a SMALL in-memory fixture corpus (NOT the production manifest) into
``TOYBOX_DATA_DIR`` via monkeypatch and drives the picker stack through every
tier of the element -> family -> theme -> untheme fallback chain. Asserts each
tier wins when (and only when) it should.

The Q5 unit suite (``tests/unit/test_reward_resolver_q5.py``) mocks
``pick_song`` / ``pick_joke`` / ``family_for`` and pins call-site sequencing.
Mocking everything below the resolver is necessary for unit-level pinning but
leaves a producer/consumer drift window — the actual picker bucket caches,
the family_for cache, and the corpus loaders are never exercised by the same
test that exercises ``_try_pick_song`` / ``_try_pick_joke``. Per
``.claude/rules/code-quality.md`` § "Audit wire shape when storage
representation changes" and § "Grep all downstream consumers ...", a real
end-to-end test that round-trips through real loaders + real cache + real
``family_for`` is the only thing that catches drift between those layers.

Strategy:

* **Option A** (real elements corpus): copy ``data/elements/elements.json``
  into the tmp_path so :func:`family_for` resolves real ids (``fe-26`` ->
  :class:`Family.transition_metal`, ``bi-83`` ->
  :class:`Family.post_transition_metal`, ``og-118`` ->
  :class:`Family.noble_gas`). Keeps the family-tier assertions exercising
  the real id -> Family map instead of a hand-rolled stub. Tiny copy
  cost; high realism.
* **Synthetic songs/jokes**: a 3-5 entry fixture corpus for each, tailored
  per-test to make exactly one tier win. The picker's bucket caches +
  ``family_for`` cache are cleared at the head of every test so the
  monkeypatched ``TOYBOX_DATA_DIR`` is observed cleanly.

The wire-shape assertion (last test) drives :func:`resolve_reward` with a
real DB so the dataclass ``ResolvedReward`` fields are verified end-to-end —
this is the regression net documented in plan-q-plan §6 D7 +
``code-quality.md`` § "Audit wire shape ...".
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest

from toybox.activities.content_resolver import (
    ResolvedReward,
    RewardActivityContext,
    _try_pick_joke,
    _try_pick_song,
    resolve_reward,
)
from toybox.activities.element_corpus import (
    Family,
    clear_element_cache,
    family_for,
)
from toybox.activities.joke_corpus import clear_joke_cache
from toybox.activities.song_corpus import clear_song_cache
from toybox.activities.themes import Theme
from toybox.core import jokes_enabled, songs_enabled
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations

# ---------------------------------------------------------------------------
# Element ids used across the chain. Each picked so the family resolves to
# a DIFFERENT canonical family — that way one fixture can answer all four
# tier-wins assertions just by switching the element_id passed to the picker.
# ---------------------------------------------------------------------------

# Iron: transition_metal. Has an element-tier fixture AND a family-tier
# fixture in the standard corpus — used to drive the element-tier win.
_IRON_ID = "fe-26"
_IRON_FAMILY = Family.transition_metal

# Bismuth: post_transition_metal. NO element-tier fixture; family-tier
# fixture present — used to drive the family-tier win.
_BISMUTH_ID = "bi-83"
_BISMUTH_FAMILY = Family.post_transition_metal

# Oganesson: noble_gas. NO element-tier fixture AND NO family-tier
# fixture (noble_gas family deliberately absent) — used to drive the
# theme-tier and untheme-tier wins.
_OGANESSON_ID = "og-118"


# ---------------------------------------------------------------------------
# Fixture corpus builders
# ---------------------------------------------------------------------------


def _song_entry(
    song_id: str,
    *,
    theme: Theme,
    element_id: str | None = None,
    family: Family | None = None,
    persona_compat: tuple[str, ...] = ("all",),
    age_band: str = "3-5",
) -> dict[str, Any]:
    """Build one valid Song manifest entry."""
    entry: dict[str, Any] = {
        "id": song_id,
        "title": song_id.replace("-", " ").title(),
        "audio_path": f"audio/{song_id}.mp3",
        "duration_seconds": 10,
        "theme": theme.value,
        "age_band": age_band,
        "persona_compat": list(persona_compat),
        "license": "CC-BY-4.0",
        "credit": "Test fixture",
        "lyrics": "la la la la la",
    }
    if element_id is not None:
        entry["element_id"] = element_id
    if family is not None:
        entry["family"] = family.value
    return entry


def _joke_entry(
    joke_id: str,
    *,
    theme: Theme,
    element_id: str | None = None,
    family: Family | None = None,
    persona_compat: tuple[str, ...] = ("all",),
    age_band: str = "3-5",
) -> dict[str, Any]:
    """Build one valid Joke jokes.json entry."""
    entry: dict[str, Any] = {
        "id": joke_id,
        "setup": f"Why did the {joke_id} do the thing?",
        "punchline": "Because it was funny.",
        "theme": theme.value,
        "optional_toy_slot": False,
        "age_band": age_band,
        "persona_compat": list(persona_compat),
    }
    if element_id is not None:
        entry["element_id"] = element_id
    if family is not None:
        entry["family"] = family.value
    return entry


def _stage_data_dir(
    tmp_path: pathlib.Path,
    *,
    songs: list[dict[str, Any]],
    jokes: list[dict[str, Any]],
) -> None:
    """Copy production ``elements.json`` into ``tmp_path`` (Option A — see
    module docstring) and write the per-test ``songs/manifest.json`` +
    ``jokes/jokes.json`` fixture corpora.

    The elements copy is mandatory because :func:`family_for` is called
    inside the picker chain; pointing ``TOYBOX_DATA_DIR`` at a tmp_path
    that lacks ``elements/elements.json`` would make ``family_for`` raise
    ``OSError`` and the picker would silently skip the family tier
    (caught by the catch-all in :func:`_resolve_family_hint`) — that
    masking would defeat the point of this smoke test.
    """
    # Production elements corpus (Option A).
    production_data_dir = pathlib.Path(__file__).resolve().parents[2] / "data"
    src_elements = production_data_dir / "elements"
    dst_elements = tmp_path / "elements"
    shutil.copytree(src_elements, dst_elements)

    # Fixture songs. Also create empty .mp3 placeholders for every entry's
    # audio_path so the picker's ``require_audio=True`` check (used by
    # :func:`_try_pick_song`) treats every entry as audio-present. The
    # file contents don't matter — only ``Path.is_file()`` is consulted.
    songs_dir = tmp_path / "songs"
    songs_dir.mkdir()
    (songs_dir / "manifest.json").write_text(json.dumps(songs), encoding="utf-8")
    audio_dir = songs_dir / "audio"
    audio_dir.mkdir()
    for song in songs:
        audio_path = song.get("audio_path")
        if isinstance(audio_path, str):
            (songs_dir / audio_path).write_bytes(b"")

    # Fixture jokes.
    jokes_dir = tmp_path / "jokes"
    jokes_dir.mkdir()
    (jokes_dir / "jokes.json").write_text(json.dumps(jokes), encoding="utf-8")


@pytest.fixture
def staged_corpora(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[pathlib.Path]:
    """Point ``TOYBOX_DATA_DIR`` at ``tmp_path`` and clear every corpus cache.

    Tests build their fixture corpora via :func:`_stage_data_dir` inside
    the test body so each test controls exactly which entries the picker
    sees. The fixture's only job is to set up the env override + cache
    clears (and clear caches AGAIN on teardown so the next test starts
    clean).
    """
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    clear_song_cache()
    clear_joke_cache()
    clear_element_cache()
    yield tmp_path
    clear_song_cache()
    clear_joke_cache()
    clear_element_cache()


@pytest.fixture
def conn(tmp_path: pathlib.Path) -> Iterator[sqlite3.Connection]:
    """Per-test SQLite DB with the full schema applied (for resolve_reward)."""
    db_path = tmp_path / "toybox.db"
    c = connect(db_path)
    try:
        run_migrations(c)
        with c:
            c.execute(
                "INSERT INTO sessions (id, started_at, ended_at) "
                "VALUES ('sess-1', '2026-01-01T00:00:00Z', NULL)"
            )
        yield c
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Standard fixture corpus shared by tier-win tests
# ---------------------------------------------------------------------------


def _standard_songs() -> list[dict[str, Any]]:
    """4 song entries covering all 4 tiers.

    * ``iron-song`` — element-keyed on ``fe-26``, theme=silly.
    * ``transition-metal-song`` — family-keyed on transition_metal, theme=silly.
    * ``post-transition-metal-song`` — family-keyed on post_transition_metal,
      theme=silly (so :func:`_BISMUTH_ID` lands here).
    * ``silly-song`` — theme=silly only (no element / no family).
    * ``untheme-song`` — theme=adventure (untheme catch-all when activity
      themes resolve to silly).
    """
    return [
        _song_entry(
            "iron-song",
            theme=Theme.silly,
            element_id=_IRON_ID,
            family=_IRON_FAMILY,
        ),
        _song_entry(
            "transition-metal-song",
            theme=Theme.silly,
            family=_IRON_FAMILY,
        ),
        _song_entry(
            "post-transition-metal-song",
            theme=Theme.silly,
            family=_BISMUTH_FAMILY,
        ),
        _song_entry("silly-song", theme=Theme.silly),
        _song_entry("untheme-song", theme=Theme.adventure),
    ]


def _standard_jokes() -> list[dict[str, Any]]:
    """Joke mirror of :func:`_standard_songs` — same 5-tier layout."""
    return [
        _joke_entry(
            "iron-joke",
            theme=Theme.silly,
            element_id=_IRON_ID,
            family=_IRON_FAMILY,
        ),
        _joke_entry(
            "transition-metal-joke",
            theme=Theme.silly,
            family=_IRON_FAMILY,
        ),
        _joke_entry(
            "post-transition-metal-joke",
            theme=Theme.silly,
            family=_BISMUTH_FAMILY,
        ),
        _joke_entry("silly-joke", theme=Theme.silly),
        _joke_entry("untheme-joke", theme=Theme.adventure),
    ]


# ---------------------------------------------------------------------------
# Sanity: the staged elements corpus produces the families we expect
# ---------------------------------------------------------------------------


def test_family_for_resolves_test_ids_when_data_dir_monkeypatched(
    staged_corpora: pathlib.Path,
) -> None:
    """Pre-flight: with TOYBOX_DATA_DIR=tmp_path and elements.json copied
    in (Option A), :func:`family_for` resolves each smoke-test element id
    to the expected canonical family.

    Pre-flight, not a tier-test — its purpose is to make a tier-tier test
    failure point at the actual broken layer (picker chain vs.
    family_for vs. tmp_path setup) instead of leaving the operator to
    guess.
    """
    # Staging fixture body — songs/jokes corpora are empty for this test;
    # we only need elements.json staged.
    _stage_data_dir(staged_corpora, songs=[], jokes=[])
    assert family_for(_IRON_ID) is _IRON_FAMILY
    assert family_for(_BISMUTH_ID) is _BISMUTH_FAMILY
    assert family_for(_OGANESSON_ID) is Family.noble_gas
    assert family_for("zz-99") is None  # unknown id


# ---------------------------------------------------------------------------
# Song tier-wins: element -> family -> theme -> untheme
# ---------------------------------------------------------------------------


def test_smoke_song_element_tier_wins(staged_corpora: pathlib.Path) -> None:
    """Element-keyed entry beats family/theme/untheme when element_id matches.

    fe-26 has both an element-keyed (``iron-song``) AND a family-keyed
    (``transition-metal-song``) fixture. The element tier MUST win.
    """
    _stage_data_dir(staged_corpora, songs=_standard_songs(), jokes=[])
    picked = _try_pick_song(
        seed=42,
        persona_id=None,
        activity_themes=[Theme.silly.value],
        element_id=_IRON_ID,
    )
    assert picked is not None
    assert picked.kind == "song"
    assert picked.reward_id == "iron-song"


def test_smoke_song_family_tier_fallback(staged_corpora: pathlib.Path) -> None:
    """No element-keyed entry for ``bi-83`` -> family-keyed
    (``post-transition-metal-song``) wins. Verifies :func:`family_for`
    resolves the id through the real production corpus end-to-end
    (Option A — see module docstring).
    """
    _stage_data_dir(staged_corpora, songs=_standard_songs(), jokes=[])
    picked = _try_pick_song(
        seed=42,
        persona_id=None,
        activity_themes=[Theme.silly.value],
        element_id=_BISMUTH_ID,
    )
    assert picked is not None
    assert picked.kind == "song"
    assert picked.reward_id == "post-transition-metal-song"


def test_smoke_song_theme_tier_fallback(staged_corpora: pathlib.Path) -> None:
    """Oganesson has no element-keyed AND no noble_gas family entry ->
    theme-tier (``silly-song``) wins.
    """
    _stage_data_dir(staged_corpora, songs=_standard_songs(), jokes=[])
    picked = _try_pick_song(
        seed=42,
        persona_id=None,
        activity_themes=[Theme.silly.value],
        element_id=_OGANESSON_ID,
    )
    assert picked is not None
    assert picked.kind == "song"
    assert picked.reward_id == "silly-song"


def test_smoke_song_untheme_tier_fallback(staged_corpora: pathlib.Path) -> None:
    """Strip every silly-themed entry — only ``untheme-song`` (adventure)
    survives. The activity themes list is ``[silly]``, so the
    theme=Theme.silly pick returns None, and the picker's
    ``theme=None`` final-fallback (the "untheme" tier per
    :func:`_try_pick_song` docstring) picks any remaining entry.
    """
    # Only the adventure-themed untheme-song remains. With element_id =
    # oganesson and no noble_gas family entry, the chain must walk past
    # element (None), family (None), theme=silly (None), then theme=None
    # which returns the adventure-themed song.
    untheme_only = [_song_entry("untheme-song", theme=Theme.adventure)]
    _stage_data_dir(staged_corpora, songs=untheme_only, jokes=[])
    picked = _try_pick_song(
        seed=42,
        persona_id=None,
        activity_themes=[Theme.silly.value],
        element_id=_OGANESSON_ID,
    )
    assert picked is not None
    assert picked.kind == "song"
    assert picked.reward_id == "untheme-song"


# ---------------------------------------------------------------------------
# Joke tier-wins (mirror of the song suite)
# ---------------------------------------------------------------------------


def test_smoke_joke_element_tier_wins(staged_corpora: pathlib.Path) -> None:
    """Joke mirror of :func:`test_smoke_song_element_tier_wins`."""
    _stage_data_dir(staged_corpora, songs=[], jokes=_standard_jokes())
    picked = _try_pick_joke(
        seed=42,
        persona_id=None,
        activity_themes=[Theme.silly.value],
        element_id=_IRON_ID,
    )
    assert picked is not None
    assert picked.kind == "joke"
    assert picked.reward_id == "iron-joke"


def test_smoke_joke_family_tier_fallback(staged_corpora: pathlib.Path) -> None:
    """Joke mirror of :func:`test_smoke_song_family_tier_fallback`."""
    _stage_data_dir(staged_corpora, songs=[], jokes=_standard_jokes())
    picked = _try_pick_joke(
        seed=42,
        persona_id=None,
        activity_themes=[Theme.silly.value],
        element_id=_BISMUTH_ID,
    )
    assert picked is not None
    assert picked.kind == "joke"
    assert picked.reward_id == "post-transition-metal-joke"


def test_smoke_joke_theme_tier_fallback(staged_corpora: pathlib.Path) -> None:
    """Joke mirror of :func:`test_smoke_song_theme_tier_fallback`."""
    _stage_data_dir(staged_corpora, songs=[], jokes=_standard_jokes())
    picked = _try_pick_joke(
        seed=42,
        persona_id=None,
        activity_themes=[Theme.silly.value],
        element_id=_OGANESSON_ID,
    )
    assert picked is not None
    assert picked.kind == "joke"
    assert picked.reward_id == "silly-joke"


def test_smoke_joke_untheme_tier_fallback(staged_corpora: pathlib.Path) -> None:
    """Joke mirror of :func:`test_smoke_song_untheme_tier_fallback`."""
    untheme_only = [_joke_entry("untheme-joke", theme=Theme.adventure)]
    _stage_data_dir(staged_corpora, songs=[], jokes=untheme_only)
    picked = _try_pick_joke(
        seed=42,
        persona_id=None,
        activity_themes=[Theme.silly.value],
        element_id=_OGANESSON_ID,
    )
    assert picked is not None
    assert picked.kind == "joke"
    assert picked.reward_id == "untheme-joke"


# ---------------------------------------------------------------------------
# Producer -> consumer round-trip: real element id from elements.json
# survives the Song.element_id Pydantic validator AND the picker bucket
# lookup. The plan-q-plan §6 D7 + code-quality.md § "Grep all downstream
# consumers" round-trip net.
# ---------------------------------------------------------------------------


def test_song_element_id_field_is_same_shape_as_element_corpus_id_format(
    staged_corpora: pathlib.Path,
) -> None:
    """Round-trip: load a real element from elements.json, write a fixture
    Song with that element's id verbatim, call _try_pick_song with the
    same id, assert the song comes back.

    Validates that the ``ELEMENT_ID_REGEX`` Pydantic constraint on
    :attr:`toybox.activities.song_corpus.Song.element_id` accepts the
    SAME shape that :class:`toybox.activities.element_corpus.Element`'s
    id validator produces — a producer/consumer drift here would silently
    drop the element-keyed song from the picker bucket (the entry would
    fail to load OR load but not be reachable via element_id).
    """
    # Build a fixture song using a real element id from the production
    # elements corpus (fe-26 / Iron — known-present in elements.json).
    real_element_id = _IRON_ID
    songs = [
        _song_entry(
            "round-trip-song",
            theme=Theme.silly,
            element_id=real_element_id,
            family=_IRON_FAMILY,
        ),
    ]
    _stage_data_dir(staged_corpora, songs=songs, jokes=[])
    # Sanity pre-condition (post-staging — the staged elements.json now
    # exists so family_for can read it).
    assert family_for(real_element_id) is not None, (
        "test pre-condition: real_element_id must resolve via family_for"
    )

    picked = _try_pick_song(
        seed=0,
        persona_id=None,
        activity_themes=[Theme.silly.value],
        element_id=real_element_id,
    )
    assert picked is not None, (
        f"round-trip failed: element_id={real_element_id!r} did not reach the picker bucket; "
        "Pydantic validator OR bucket key shape drift"
    )
    assert picked.reward_id == "round-trip-song"


# ---------------------------------------------------------------------------
# Wire-shape regression: drive :func:`resolve_reward` end-to-end and pin
# the exact set of ResolvedReward dataclass fields. The Q5 unit suite has
# a similar assertion but with a mocked pick_song; this version exercises
# the REAL corpus loaders and resolve_reward's full DB-touching path.
# ---------------------------------------------------------------------------


def test_resolve_reward_wire_shape_for_element_aware_song(
    staged_corpora: pathlib.Path,
    conn: sqlite3.Connection,
) -> None:
    """After :func:`resolve_reward` for an element-aware activity, the
    :class:`ResolvedReward` dataclass fields MUST be exactly the eight
    documented in :class:`ResolvedReward`. Any drift (added field,
    removed field, renamed field) silently breaks the kiosk wire
    envelope per ``code-quality.md`` § "Audit wire shape ...".
    """
    songs_enabled.set(conn, True)
    jokes_enabled.set(conn, False)
    _stage_data_dir(staged_corpora, songs=_standard_songs(), jokes=[])

    ctx = RewardActivityContext(
        id="act-q6-wire",
        session_id="sess-1",
        persona_id=None,
        slot_fills_json=None,
        current_step_count=1,
        element_id=_IRON_ID,
    )
    result = resolve_reward(conn, ctx, "song")
    assert result is not None
    assert isinstance(result, ResolvedReward)
    # Exact-set assertion: no added fields, no removed fields.
    field_names = set(result.__dataclass_fields__)
    assert field_names == {
        "kind",
        "reward_id",
        "image_url",
        "animation",
        "audio_url",
        "body",
        "setup",
        "punchline",
    }, f"ResolvedReward field-set drift: {field_names!r}"
    # Spot-check the element-tier win actually happened end-to-end (so a
    # future regression that silently bypasses the element tier doesn't
    # quietly pass the wire-shape check).
    assert result.kind == "song"
    assert result.reward_id == "iron-song"
    assert result.audio_url == "/api/static/songs/audio/iron-song.mp3"
