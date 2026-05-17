"""Phase L Step L3 — unit tests for the reward resolver.

Tests cover the L3 algorithm contract:

* Resolution by requested type (picture / joke / song / random) with
  fallback walking ``picture -> joke -> song -> None`` when the
  starting type yields nothing.
* Theme intersection scoring with the documented multi-key sort:
  overlap_count DESC, last_used_at ASC NULLS FIRST, id ASC.
* Empty-intersection uniform fallback over the picture pool.
* Determinism: same ``(activity_id, current_step_count)`` returns the
  same :class:`ResolvedReward`.
* The shared ``recent_transcript_texts`` helper's LIMIT behaviour.

The tests use a real in-memory SQLite DB seeded by hand (via the
``conn`` fixture) so the SQL is exercised, but mock
:func:`extract_themes` and the joke/song corpus pickers so the tests
don't depend on the bundled corpora — that lets the assertions pin the
exact picks without re-deriving the theme extraction on each run.

Mirrors the fixture / mocking conventions in
``tests/unit/test_content_resolver.py``.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from toybox.activities.content_resolver import (
    ResolvedReward,
    RewardActivityContext,
    recent_transcript_texts,
    resolve_reward,
)
from toybox.activities.joke_corpus import Joke
from toybox.activities.models import Animation
from toybox.activities.song_corpus import Song
from toybox.activities.themes import Theme
from toybox.core import jokes_enabled, songs_enabled
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations

# ---------------------------------------------------------------------
# Fixtures + seed helpers
# ---------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path: pathlib.Path) -> Iterator[sqlite3.Connection]:
    """Per-test SQLite DB with the full schema applied."""
    db_path = tmp_path / "toybox.db"
    c = connect(db_path)
    try:
        run_migrations(c)
        # Sessions / activities tables have FK constraints; insert a
        # session row up front so per-test activity inserts succeed.
        with c:
            c.execute(
                "INSERT INTO sessions (id, started_at, ended_at) "
                "VALUES ('sess-1', '2026-01-01T00:00:00Z', NULL)"
            )
        yield c
    finally:
        c.close()


def _insert_reward(
    conn: sqlite3.Connection,
    *,
    reward_id: str,
    display_name: str = "A Reward",
    tags: list[str] | None = None,
    animation: Animation = Animation.shine,
    active: int = 1,
    archived: int = 0,
    last_used_at: str | None = None,
) -> None:
    """Insert a reward row with sensible defaults."""
    tags_json = json.dumps(tags or [])
    with conn:
        conn.execute(
            "INSERT INTO rewards "
            "(id, display_name, image_path, image_hash, tags, animation, "
            " active, archived, created_at, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00Z', ?)",
            (
                reward_id,
                display_name,
                f"data/images/rewards/{reward_id}.png",
                f"hash-{reward_id}",
                tags_json,
                animation.value,
                active,
                archived,
                last_used_at,
            ),
        )


def _insert_transcript(
    conn: sqlite3.Connection,
    *,
    transcript_id: str,
    text: str,
    session_id: str = "sess-1",
    ended_at: str = "2026-01-01T00:00:00Z",
) -> None:
    """Insert one transcript row in ``session_id``."""
    with conn:
        conn.execute(
            "INSERT INTO transcripts "
            "(id, session_id, mic_id, started_at, ended_at, text) "
            "VALUES (?, ?, NULL, ?, ?, ?)",
            (transcript_id, session_id, ended_at, ended_at, text),
        )


def _ctx(
    *,
    activity_id: str = "act-1",
    session_id: str = "sess-1",
    persona_id: str | None = "wizard",
    template_id: str | None = None,
    step_count: int = 5,
) -> RewardActivityContext:
    """Build a :class:`RewardActivityContext` snapshot for a test."""
    slot_fills: dict[str, Any] = {}
    if template_id is not None:
        slot_fills["__template_id"] = template_id
    return RewardActivityContext(
        id=activity_id,
        session_id=session_id,
        persona_id=persona_id,
        slot_fills_json=json.dumps(slot_fills) if slot_fills else None,
        current_step_count=step_count,
    )


def _fake_joke(joke_id: str = "fake-joke-1") -> Joke:
    return Joke(
        id=joke_id,
        setup="Why did the joke cross?",
        punchline="To make the test pass.",
        theme=Theme.silly,
        optional_toy_slot=False,
        age_band="3-5",
        persona_compat=("all",),
    )


def _fake_song(song_id: str = "fake-song-1") -> Song:
    return Song(
        id=song_id,
        title="Fake Song",
        audio_path=f"audio/{song_id}.mp3",
        duration_seconds=10,
        theme=Theme.music,
        age_band="3-5",
        persona_compat=("all",),
        license="CC-BY-4.0",
        credit="Test",
        lyrics="la la la",
    )


# ---------------------------------------------------------------------
# Test 1: requested_type="picture" with one matching reward
# ---------------------------------------------------------------------


def test_picture_returns_matching_reward(conn: sqlite3.Connection) -> None:
    _insert_reward(conn, reward_id="trophy", display_name="Gold Trophy", tags=["adventure"])
    # Mock extract_themes to return [adventure] so the picture overlaps.
    with patch(
        "toybox.activities.content_resolver.extract_themes",
        return_value=[Theme.adventure],
    ):
        result = resolve_reward(conn, _ctx(), "picture")
    assert result is not None
    assert result.kind == "picture"
    assert result.reward_id == "trophy"
    assert result.image_url == "/api/static/images/rewards/trophy.png"
    assert result.animation is Animation.shine
    assert result.body == "Gold Trophy"
    assert result.audio_url is None
    assert result.setup is None
    assert result.punchline is None


# ---------------------------------------------------------------------
# Test 2: picture with no rewards falls back to joke
# ---------------------------------------------------------------------


def test_picture_falls_back_to_joke_when_no_rewards(conn: sqlite3.Connection) -> None:
    # No rewards inserted. Mock the joke picker so it returns one.
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch(
            "toybox.activities.content_resolver.pick_joke",
            return_value=_fake_joke("knock-knock"),
        ),
    ):
        result = resolve_reward(conn, _ctx(), "picture")
    assert result is not None
    assert result.kind == "joke"
    assert result.reward_id == "knock-knock"
    assert result.body == "To make the test pass."
    assert result.setup == "Why did the joke cross?"
    assert result.punchline == "To make the test pass."
    assert result.image_url is None
    assert result.audio_url is None
    assert result.animation is None


# ---------------------------------------------------------------------
# Test 3: picture, no rewards, jokes_enabled=False -> song
# ---------------------------------------------------------------------


def test_picture_falls_back_to_song_when_jokes_disabled(conn: sqlite3.Connection) -> None:
    jokes_enabled.set(conn, False)
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch(
            "toybox.activities.content_resolver.pick_joke",
            return_value=None,
        ),
        patch(
            "toybox.activities.content_resolver.pick_song",
            return_value=_fake_song("rocket-song"),
        ),
    ):
        result = resolve_reward(conn, _ctx(), "picture")
    assert result is not None
    assert result.kind == "song"
    assert result.reward_id == "rocket-song"
    assert result.audio_url == "/api/static/songs/audio/rocket-song.mp3"
    assert result.body == "Fake Song"


# ---------------------------------------------------------------------
# Test 4: picture, no rewards, jokes off, songs empty -> None
# ---------------------------------------------------------------------


def test_returns_none_when_everything_empty(conn: sqlite3.Connection) -> None:
    jokes_enabled.set(conn, False)
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch(
            "toybox.activities.content_resolver.pick_joke",
            return_value=None,
        ),
        patch(
            "toybox.activities.content_resolver.pick_song",
            return_value=None,
        ),
    ):
        result = resolve_reward(conn, _ctx(), "picture")
    assert result is None


# ---------------------------------------------------------------------
# Test 5: requested_type="joke" returns a joke when corpus non-empty
# ---------------------------------------------------------------------


def test_joke_request_returns_joke(conn: sqlite3.Connection) -> None:
    # No rewards; mock joke picker so we exercise the joke path.
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch(
            "toybox.activities.content_resolver.pick_joke",
            return_value=_fake_joke("riddle-1"),
        ),
    ):
        result = resolve_reward(conn, _ctx(), "joke")
    assert result is not None
    assert result.kind == "joke"
    assert result.reward_id == "riddle-1"


# ---------------------------------------------------------------------
# Test 6: requested_type="song", songs_enabled=False -> falls back
# to picture when rewards exist
# ---------------------------------------------------------------------


def test_song_request_falls_back_to_picture_when_songs_disabled(
    conn: sqlite3.Connection,
) -> None:
    songs_enabled.set(conn, False)
    _insert_reward(conn, reward_id="cookie", display_name="Cookie", tags=["food"])
    # Songs flag is irrelevant for an explicit "song" request — the
    # fallback chain walks the remaining types when the song corpus
    # picker returns None. Mock the picker to return None to force
    # the chain to step through picture (which we DO have).
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch(
            "toybox.activities.content_resolver.pick_song",
            return_value=None,
        ),
    ):
        result = resolve_reward(conn, _ctx(), "song")
    assert result is not None
    assert result.kind == "picture"
    assert result.reward_id == "cookie"


# ---------------------------------------------------------------------
# Test 7: random over 30 trials yields all three types
# ---------------------------------------------------------------------


def test_random_distribution_is_non_degenerate(conn: sqlite3.Connection) -> None:
    # All three types eligible: insert a picture reward, leave the
    # household flags at the default ``True``, and mock the joke +
    # song pickers to always return an entry.
    _insert_reward(conn, reward_id="trophy", display_name="Trophy")
    counts: dict[str, int] = {"picture": 0, "joke": 0, "song": 0}
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch(
            "toybox.activities.content_resolver.pick_joke",
            return_value=_fake_joke("j1"),
        ),
        patch(
            "toybox.activities.content_resolver.pick_song",
            return_value=_fake_song("s1"),
        ),
    ):
        for step in range(30):
            ctx = _ctx(step_count=step)
            result = resolve_reward(conn, ctx, "random")
            assert result is not None
            counts[result.kind] += 1
    # Every type appears at least twice. The seed varies per step so
    # the distribution is non-degenerate by design.
    assert counts["picture"] >= 2, counts
    assert counts["joke"] >= 2, counts
    assert counts["song"] >= 2, counts


# ---------------------------------------------------------------------
# Test 8: random is deterministic for same (activity_id, step_count)
# ---------------------------------------------------------------------


def test_random_is_deterministic_for_same_seed(conn: sqlite3.Connection) -> None:
    _insert_reward(conn, reward_id="trophy", display_name="Trophy")
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch(
            "toybox.activities.content_resolver.pick_joke",
            return_value=_fake_joke("j1"),
        ),
        patch(
            "toybox.activities.content_resolver.pick_song",
            return_value=_fake_song("s1"),
        ),
    ):
        a = resolve_reward(conn, _ctx(activity_id="act-42", step_count=7), "random")
        b = resolve_reward(conn, _ctx(activity_id="act-42", step_count=7), "random")
    assert a == b


# ---------------------------------------------------------------------
# Test 9: theme tag-match uses UNION of template + transcript themes
# ---------------------------------------------------------------------


def test_theme_union_covers_template_and_transcript(conn: sqlite3.Connection) -> None:
    # Two rewards: one tagged 'pirates' (intersects transcript-source
    # only), one tagged 'adventure' (intersects template-source only).
    # The UNION should make BOTH rewards overlap=1 — neither side of
    # the union should silently drop. We verify each side of the union
    # is independently load-bearing by toggling which reward is
    # present.
    _insert_reward(conn, reward_id="r-pirate", display_name="Pirate", tags=["pirates"])
    _insert_reward(conn, reward_id="r-adv", display_name="Adventurer", tags=["adventure"])

    class _StubTemplate:
        id = "tpl-1"
        recommended_themes = (Theme.adventure,)

    # Trial 1: both rewards present. With overlap_count=1 each and
    # last_used_at=NULL on both, id-ASC tiebreaks to ``r-adv``. The
    # adventure side of the union is reached.
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[Theme.pirates],
        ),
        patch(
            "toybox.activities.generator.find_template_by_id",
            return_value=_StubTemplate(),
        ),
    ):
        result = resolve_reward(conn, _ctx(template_id="tpl-1"), "picture")
    assert result is not None
    assert result.reward_id == "r-adv", "adventure (template-source) should reach overlap=1"

    # Trial 2: remove r-adv. Only r-pirate remains. If the union
    # silently dropped the transcript-source side (pirates), r-pirate
    # would have overlap=0 and fall through to the uniform-random
    # branch — but with only one reward in the pool, that still picks
    # r-pirate, so we need a sharper probe. Add a no-overlap reward as
    # a tiebreak control; r-pirate's overlap=1 must beat r-other's
    # overlap=0 — proving the transcript-source side of the union is
    # alive.
    with conn:
        conn.execute("DELETE FROM rewards WHERE id = 'r-adv'")
    _insert_reward(conn, reward_id="r-other", display_name="Other", tags=["food"])
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[Theme.pirates],
        ),
        patch(
            "toybox.activities.generator.find_template_by_id",
            return_value=_StubTemplate(),
        ),
    ):
        result2 = resolve_reward(conn, _ctx(template_id="tpl-1"), "picture")
    assert result2 is not None
    assert result2.reward_id == "r-pirate", (
        "pirates (transcript-source) should be in the union and beat overlap=0 r-other"
    )


# ---------------------------------------------------------------------
# Test 10: empty intersection -> uniform random over picture pool
# ---------------------------------------------------------------------


def test_empty_intersection_uniform_random(conn: sqlite3.Connection) -> None:
    # 3 rewards, none tagged with any activity theme.
    _insert_reward(conn, reward_id="a", display_name="A", tags=["space"])
    _insert_reward(conn, reward_id="b", display_name="B", tags=["food"])
    _insert_reward(conn, reward_id="c", display_name="C", tags=["weather"])
    seen: set[str] = set()
    with patch(
        "toybox.activities.content_resolver.extract_themes",
        return_value=[Theme.pirates],  # doesn't intersect any reward
    ):
        for step in range(30):
            result = resolve_reward(conn, _ctx(step_count=step), "picture")
            assert result is not None
            seen.add(result.reward_id)
    assert seen == {"a", "b", "c"}, f"expected all 3 to be reachable; got {seen}"


# ---------------------------------------------------------------------
# Test 11: recent_transcript_texts honours LIMIT and recency order
# ---------------------------------------------------------------------


def test_recent_transcript_texts_returns_50_most_recent(conn: sqlite3.Connection) -> None:
    for i in range(60):
        _insert_transcript(
            conn,
            transcript_id=f"t-{i:03d}",
            text=f"snippet-{i}",
            ended_at=f"2026-01-01T00:{i:02d}:00Z",
        )
    out = recent_transcript_texts(conn, "sess-1", limit=50)
    assert len(out) == 50
    # ``ORDER BY ended_at DESC`` → most-recent first.
    assert out[0] == "snippet-59"
    assert out[-1] == "snippet-10"


# ---------------------------------------------------------------------
# Test 12: last_used_at tiebreak — NULL first, then oldest first
# ---------------------------------------------------------------------


def test_last_used_at_tiebreak(conn: sqlite3.Connection) -> None:
    # 3 rewards, equal overlap (all tagged 'magic'), different
    # last_used_at: one NULL, one old, one recent.
    _insert_reward(
        conn,
        reward_id="never",
        display_name="Never Used",
        tags=["magic"],
        last_used_at=None,
    )
    _insert_reward(
        conn,
        reward_id="old",
        display_name="Old",
        tags=["magic"],
        last_used_at="2025-01-01T00:00:00Z",
    )
    _insert_reward(
        conn,
        reward_id="recent",
        display_name="Recent",
        tags=["magic"],
        last_used_at="2026-05-01T00:00:00Z",
    )
    with patch(
        "toybox.activities.content_resolver.extract_themes",
        return_value=[Theme.magic],
    ):
        result = resolve_reward(conn, _ctx(), "picture")
    # NULLS FIRST → "never" wins outright.
    assert result is not None
    assert result.reward_id == "never"

    # Remove the NULL row and re-resolve — the older row should win.
    with conn:
        conn.execute("DELETE FROM rewards WHERE id = 'never'")
    with patch(
        "toybox.activities.content_resolver.extract_themes",
        return_value=[Theme.magic],
    ):
        result2 = resolve_reward(conn, _ctx(), "picture")
    assert result2 is not None
    assert result2.reward_id == "old"


# ---------------------------------------------------------------------
# Bonus: template id absent → resolver still works (no crash)
# ---------------------------------------------------------------------


def test_resolver_tolerates_missing_template_id(conn: sqlite3.Connection) -> None:
    _insert_reward(conn, reward_id="r1", display_name="R1", tags=["adventure"])
    # ctx with no template_id → ``__template_id`` not in slot_fills →
    # template-themes source returns []; transcript source is the only
    # contributor.
    with patch(
        "toybox.activities.content_resolver.extract_themes",
        return_value=[Theme.adventure],
    ):
        result = resolve_reward(conn, _ctx(template_id=None), "picture")
    assert result is not None
    assert result.reward_id == "r1"


def test_resolver_tolerates_unknown_template_id(conn: sqlite3.Connection) -> None:
    _insert_reward(conn, reward_id="r1", display_name="R1", tags=[])
    # Real find_template_by_id used; template id won't be loaded.
    with patch(
        "toybox.activities.content_resolver.extract_themes",
        return_value=[],
    ):
        result = resolve_reward(conn, _ctx(template_id="ghost-template"), "picture")
    assert result is not None
    assert result.reward_id == "r1"


# ---------------------------------------------------------------------
# Bonus: shape — ResolvedReward fields populate by kind
# ---------------------------------------------------------------------


def test_picture_resolved_shape_complete(conn: sqlite3.Connection) -> None:
    _insert_reward(
        conn,
        reward_id="trophy",
        display_name="Trophy",
        tags=[],
        animation=Animation.jump,
    )
    with patch(
        "toybox.activities.content_resolver.extract_themes",
        return_value=[],
    ):
        result = resolve_reward(conn, _ctx(), "picture")
    assert isinstance(result, ResolvedReward)
    assert result.kind == "picture"
    assert result.animation is Animation.jump
    assert result.image_url == "/api/static/images/rewards/trophy.png"
    assert result.audio_url is None


# ---------------------------------------------------------------------
# Flag-gate tests (Finding 1, HIGH): ``jokes_enabled`` /
# ``songs_enabled`` MUST hard-gate the explicit-type AND fallback
# paths, not just the random-roll eligibility list.
# ---------------------------------------------------------------------


def test_explicit_joke_request_skipped_when_jokes_disabled(conn: sqlite3.Connection) -> None:
    """``requested_type="joke"`` + ``jokes_enabled=false`` → fall to picture."""
    jokes_enabled.set(conn, False)
    _insert_reward(conn, reward_id="cookie", display_name="Cookie", tags=["food"])
    mock_joke = MagicMock(return_value=_fake_joke("should-not-fire"))
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch("toybox.activities.content_resolver.pick_joke", mock_joke),
    ):
        result = resolve_reward(conn, _ctx(), "joke")
    assert result is not None
    assert result.kind == "picture", (
        "joke type should be gated off; fallback chain should reach picture"
    )
    assert result.reward_id == "cookie"
    # pick_joke MUST NOT have been called — the flag gate skipped joke entirely.
    mock_joke.assert_not_called()


def test_explicit_joke_request_jokes_off_no_rewards_falls_to_song(
    conn: sqlite3.Connection,
) -> None:
    """``requested_type="joke"`` + ``jokes_enabled=false`` + no rewards → song."""
    jokes_enabled.set(conn, False)
    mock_joke = MagicMock(return_value=_fake_joke("should-not-fire"))
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch("toybox.activities.content_resolver.pick_joke", mock_joke),
        patch(
            "toybox.activities.content_resolver.pick_song",
            return_value=_fake_song("salvage-song"),
        ),
    ):
        result = resolve_reward(conn, _ctx(), "joke")
    assert result is not None
    assert result.kind == "song"
    assert result.reward_id == "salvage-song"
    mock_joke.assert_not_called()


def test_explicit_song_request_skipped_when_songs_disabled(conn: sqlite3.Connection) -> None:
    """``requested_type="song"`` + ``songs_enabled=false`` → fall to picture."""
    songs_enabled.set(conn, False)
    _insert_reward(conn, reward_id="cookie", display_name="Cookie", tags=["food"])
    mock_song = MagicMock(return_value=_fake_song("should-not-fire"))
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch("toybox.activities.content_resolver.pick_song", mock_song),
    ):
        result = resolve_reward(conn, _ctx(), "song")
    assert result is not None
    assert result.kind == "picture"
    assert result.reward_id == "cookie"
    mock_song.assert_not_called()


def test_explicit_song_request_songs_off_no_rewards_falls_to_joke(
    conn: sqlite3.Connection,
) -> None:
    """``requested_type="song"`` + ``songs_enabled=false`` + no rewards → joke."""
    songs_enabled.set(conn, False)
    mock_song = MagicMock(return_value=_fake_song("should-not-fire"))
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch(
            "toybox.activities.content_resolver.pick_joke",
            return_value=_fake_joke("salvage-joke"),
        ),
        patch("toybox.activities.content_resolver.pick_song", mock_song),
    ):
        result = resolve_reward(conn, _ctx(), "song")
    assert result is not None
    assert result.kind == "joke"
    assert result.reward_id == "salvage-joke"
    mock_song.assert_not_called()


def test_picture_fallback_skips_joke_when_jokes_disabled(conn: sqlite3.Connection) -> None:
    """Picture-fallback into joke with ``jokes_enabled=false`` → continues to song.

    No rewards exist, so the picture starting type fails. The fallback
    chain steps to joke — but jokes are disabled, so it MUST skip joke
    (without calling ``pick_joke``) and step to song.
    """
    jokes_enabled.set(conn, False)
    mock_joke = MagicMock(return_value=_fake_joke("should-not-fire"))
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch("toybox.activities.content_resolver.pick_joke", mock_joke),
        patch(
            "toybox.activities.content_resolver.pick_song",
            return_value=_fake_song("salvage-song"),
        ),
    ):
        result = resolve_reward(conn, _ctx(), "picture")
    assert result is not None
    assert result.kind == "song"
    mock_joke.assert_not_called()


# ---------------------------------------------------------------------
# Corpus-exception tests (Finding 2, medium): ``pick_joke`` /
# ``pick_song`` raises during the pick → caught, type treated as miss,
# fallback proceeds.
# ---------------------------------------------------------------------


def test_pick_joke_value_error_falls_through_to_next_type(conn: sqlite3.Connection) -> None:
    """``pick_joke`` raises ``ValueError`` → fall through to song."""
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch(
            "toybox.activities.content_resolver.pick_joke",
            side_effect=ValueError("malformed jokes.json"),
        ),
        patch(
            "toybox.activities.content_resolver.pick_song",
            return_value=_fake_song("salvage-song"),
        ),
    ):
        result = resolve_reward(conn, _ctx(), "joke")
    assert result is not None
    assert result.kind == "song"
    assert result.reward_id == "salvage-song"


def test_pick_song_os_error_falls_through(conn: sqlite3.Connection) -> None:
    """``pick_song`` raises ``OSError`` → fall through. No more types → None."""
    jokes_enabled.set(conn, False)
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch(
            "toybox.activities.content_resolver.pick_song",
            side_effect=OSError("audio dir gone"),
        ),
    ):
        # No rewards, jokes off → only song eligible. Song raises →
        # caught → no more types → None.
        result = resolve_reward(conn, _ctx(), "song")
    assert result is None


# ---------------------------------------------------------------------
# Test gap: ``require_audio=True`` is passed to ``pick_song``.
# ---------------------------------------------------------------------


def test_pick_song_called_with_require_audio_true(conn: sqlite3.Connection) -> None:
    mock_song = MagicMock(return_value=_fake_song("s1"))
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch("toybox.activities.content_resolver.pick_song", mock_song),
    ):
        result = resolve_reward(conn, _ctx(), "song")
    assert result is not None
    assert result.kind == "song"
    # At least one call with require_audio=True.
    assert mock_song.call_count >= 1
    for call in mock_song.call_args_list:
        assert call.kwargs.get("require_audio") is True, (
            f"pick_song must always be called with require_audio=True; got {call}"
        )


# ---------------------------------------------------------------------
# Test gap: "Lowest-id Theme" picker policy is exercised in a joke
# test. ``Theme`` declaration order (themes.py): adventure, magic,
# space, animals, vehicles, food, friendship, pirates, knights,
# weather, music, silly. So among ``[silly, pirates]``, pirates wins
# (declared first).
# ---------------------------------------------------------------------


def test_pick_joke_called_with_lowest_id_theme(conn: sqlite3.Connection) -> None:
    """When activity themes are ``[silly, pirates]``, picker theme arg = pirates.

    Pirates is declared before silly in :class:`Theme`, so the
    "lowest-id Theme" policy picks pirates.
    """
    mock_joke = MagicMock(return_value=_fake_joke("j1"))
    # extract_themes returns the transcript-driven themes in
    # declaration-order-agnostic form; the resolver internally re-sorts
    # by Theme enum order before picking. Pass silly first to prove
    # the resolver doesn't rely on input order.
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[Theme.silly, Theme.pirates],
        ),
        patch("toybox.activities.content_resolver.pick_joke", mock_joke),
    ):
        result = resolve_reward(conn, _ctx(), "joke")
    assert result is not None
    # First call to pick_joke is the themed try.
    first_call = mock_joke.call_args_list[0]
    assert first_call.kwargs.get("theme") is Theme.pirates, (
        f"expected pirates (declared first in Theme); got {first_call.kwargs.get('theme')}"
    )


# ---------------------------------------------------------------------
# Test gap: determinism for explicit (non-random) types — same
# ``(activity_id, step_count)`` + same ``requested_type="joke"`` →
# identical result.
# ---------------------------------------------------------------------


def test_explicit_joke_is_deterministic_for_same_seed(conn: sqlite3.Connection) -> None:
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[],
        ),
        patch(
            "toybox.activities.content_resolver.pick_joke",
            return_value=_fake_joke("j1"),
        ),
    ):
        a = resolve_reward(conn, _ctx(activity_id="act-42", step_count=7), "joke")
        b = resolve_reward(conn, _ctx(activity_id="act-42", step_count=7), "joke")
    assert a == b
    assert a is not None
    assert a.kind == "joke"


# ---------------------------------------------------------------------
# L follow-up Change D — ``requested_type="none"`` short-circuits.
# Distinct from NULL on the column: the resolver is even called with
# the value, but it returns ``None`` immediately without touching the
# theme/load paths.
# ---------------------------------------------------------------------


def test_requested_type_none_returns_none_with_active_rewards(
    conn: sqlite3.Connection,
) -> None:
    """Even with picture rewards available, ``"none"`` returns ``None``."""
    _insert_reward(conn, reward_id="trophy", display_name="Trophy", tags=["adventure"])
    # No theme mock needed — the short-circuit happens before
    # ``_compute_activity_themes`` would call ``extract_themes``.
    result = resolve_reward(conn, _ctx(), "none")  # type: ignore[arg-type]
    assert result is None


def test_requested_type_none_returns_none_with_empty_pools(
    conn: sqlite3.Connection,
) -> None:
    """``"none"`` short-circuits regardless of pool state."""
    result = resolve_reward(conn, _ctx(), "none")  # type: ignore[arg-type]
    assert result is None


# ---------------------------------------------------------------------
# L follow-up Change E — picture branch honours the pinned
# ``__reward_id`` from ``slot_fills_json`` when the reward is in the
# active pool. Falls back to the random tag-match pick when the pinned
# reward is missing / archived / deleted between approve and play.
# ---------------------------------------------------------------------


def _ctx_with_pin(
    *,
    pinned_id: str,
    activity_id: str = "act-1",
    template_id: str | None = None,
) -> RewardActivityContext:
    """Build a context whose slot_fills_json carries ``__reward_id``."""
    slot_fills: dict[str, Any] = {"__reward_id": pinned_id}
    if template_id is not None:
        slot_fills["__template_id"] = template_id
    return RewardActivityContext(
        id=activity_id,
        session_id="sess-1",
        persona_id=None,
        slot_fills_json=json.dumps(slot_fills),
        current_step_count=5,
    )


def test_picture_pin_returns_pinned_reward(conn: sqlite3.Connection) -> None:
    """Pinned id present in the active pool → resolver returns THAT reward."""
    # Three rewards. Without the pin, the theme-overlap sort would
    # pick whichever overlaps best; the pin must override that order.
    _insert_reward(conn, reward_id="r-popular", display_name="Popular", tags=["adventure"])
    _insert_reward(conn, reward_id="r-pinned", display_name="Pinned", tags=[])
    _insert_reward(conn, reward_id="r-other", display_name="Other", tags=["food"])
    with patch(
        "toybox.activities.content_resolver.extract_themes",
        return_value=[Theme.adventure],
    ):
        result = resolve_reward(conn, _ctx_with_pin(pinned_id="r-pinned"), "picture")
    assert result is not None
    assert result.kind == "picture"
    assert result.reward_id == "r-pinned"
    # Display name + image URL also reflect the pinned row, not the
    # would-have-won "Popular" overlap.
    assert result.body == "Pinned"


def test_picture_pin_falls_back_when_pin_missing(conn: sqlite3.Connection) -> None:
    """Pinned id absent from active pool → fall back to random tag-match.

    Models the "deleted between approve and play" race per operator's
    decision (resolver MUST still fire a reward, not refuse).
    """
    # Active pool has one reward, NOT the one pinned.
    _insert_reward(conn, reward_id="r-other", display_name="Other", tags=["adventure"])
    with patch(
        "toybox.activities.content_resolver.extract_themes",
        return_value=[Theme.adventure],
    ):
        result = resolve_reward(
            conn,
            _ctx_with_pin(pinned_id="r-archived-or-gone"),
            "picture",
        )
    assert result is not None
    assert result.reward_id == "r-other"


def test_picture_pin_falls_back_when_pin_archived(conn: sqlite3.Connection) -> None:
    """Archived rewards are filtered by the SQL — pin against one falls back."""
    # The "pinned" reward is archived → never loaded → pin missed → fall back.
    _insert_reward(conn, reward_id="r-pinned", display_name="Pinned", tags=[], archived=1)
    _insert_reward(conn, reward_id="r-active", display_name="Active", tags=["adventure"])
    with patch(
        "toybox.activities.content_resolver.extract_themes",
        return_value=[Theme.adventure],
    ):
        result = resolve_reward(conn, _ctx_with_pin(pinned_id="r-pinned"), "picture")
    assert result is not None
    assert result.reward_id == "r-active"


def test_picture_pin_falls_back_when_pin_inactive(conn: sqlite3.Connection) -> None:
    """Inactive rewards (active=0) likewise fall back, matching the SQL filter."""
    _insert_reward(conn, reward_id="r-pinned", display_name="Pinned", tags=[], active=0)
    _insert_reward(conn, reward_id="r-live", display_name="Live", tags=["adventure"])
    with patch(
        "toybox.activities.content_resolver.extract_themes",
        return_value=[Theme.adventure],
    ):
        result = resolve_reward(conn, _ctx_with_pin(pinned_id="r-pinned"), "picture")
    assert result is not None
    assert result.reward_id == "r-live"
