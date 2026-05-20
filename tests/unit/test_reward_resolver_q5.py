"""Phase Q Step Q5 — element-aware reward picker chain tests.

Sibling file to ``test_reward_resolver.py`` so the L3 test file stays
focused on the picture/joke/song/random fallback contract while the
Q5 element → family → theme chain has its own dedicated suite. Shares
no fixtures with the L3 file — each test builds its own SQLite +
RewardActivityContext.

The tests mock ``pick_song`` / ``pick_joke`` / ``family_for`` so the
assertions pin the exact picker call sequence without depending on the
shipped corpus's element coverage (which is sparse pre-Q7).
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from toybox.activities.content_resolver import (
    ResolvedReward,
    RewardActivityContext,
    resolve_reward,
)
from toybox.activities.element_corpus import Family
from toybox.activities.joke_corpus import Joke
from toybox.activities.song_corpus import Song
from toybox.activities.themes import Theme
from toybox.core import jokes_enabled
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations


@pytest.fixture
def conn(tmp_path: pathlib.Path) -> Iterator[sqlite3.Connection]:
    """Per-test SQLite DB with the full schema applied."""
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


def _fake_joke(joke_id: str) -> Joke:
    return Joke(
        id=joke_id,
        setup="Setup?",
        punchline="Punchline.",
        theme=Theme.silly,
        optional_toy_slot=False,
        age_band="3-5",
        persona_compat=("all",),
    )


def _fake_song(song_id: str) -> Song:
    return Song(
        id=song_id,
        title="A Song",
        audio_path=f"audio/{song_id}.mp3",
        duration_seconds=10,
        theme=Theme.space,
        age_band="3-5",
        persona_compat=("all",),
        license="CC-BY-4.0",
        credit="Test",
        lyrics="la la la",
    )


def _ctx_with_element(
    element_id: str | None,
    *,
    activity_id: str = "act-1",
    persona_id: str | None = "wizard",
    step_count: int = 5,
) -> RewardActivityContext:
    """Build a context carrying ``element_id`` for the element-aware chain tests."""
    return RewardActivityContext(
        id=activity_id,
        session_id="sess-1",
        persona_id=persona_id,
        slot_fills_json=None,
        current_step_count=step_count,
        element_id=element_id,
    )


# ---------------------------------------------------------------------
# _try_pick_song — element → family → theme chain
# ---------------------------------------------------------------------


def test_try_pick_song_passes_element_id_when_ctx_has_one(
    conn: sqlite3.Connection,
) -> None:
    """ctx.element_id set → pick_song's FIRST call must carry element_id."""
    jokes_enabled.set(conn, False)
    fake = _fake_song("element-tier-song")
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[Theme.space],
        ),
        patch(
            "toybox.activities.content_resolver.pick_song",
            return_value=fake,
        ) as mock_pick,
    ):
        result = resolve_reward(conn, _ctx_with_element("ne-10"), "song")
    assert result is not None
    assert result.kind == "song"
    assert result.reward_id == "element-tier-song"
    first_call = mock_pick.call_args_list[0]
    assert first_call.kwargs.get("element_id") == "ne-10"


def test_try_pick_song_falls_back_to_family_when_element_no_match(
    conn: sqlite3.Connection,
) -> None:
    """Element-tier returns None → family-tier called with family_hint
    resolved via family_for.
    """
    jokes_enabled.set(conn, False)
    fake = _fake_song("family-tier-song")

    def picker(seed: int, **kwargs: Any) -> Song | None:
        if kwargs.get("element_id") is not None:
            return None
        if kwargs.get("family_hint") is Family.noble_gas:
            return fake
        return None

    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[Theme.space],
        ),
        patch(
            "toybox.activities.content_resolver.pick_song",
            side_effect=picker,
        ) as mock_pick,
        patch(
            "toybox.activities.content_resolver.family_for",
            return_value=Family.noble_gas,
        ) as mock_family,
    ):
        result = resolve_reward(conn, _ctx_with_element("ne-10"), "song")
    assert result is not None
    assert result.reward_id == "family-tier-song"
    mock_family.assert_called_with("ne-10")
    assert len(mock_pick.call_args_list) >= 2
    family_call = mock_pick.call_args_list[1]
    assert family_call.kwargs.get("family_hint") is Family.noble_gas


def test_try_pick_song_falls_back_to_theme_when_neither_element_nor_family_match(
    conn: sqlite3.Connection,
) -> None:
    """Element-tier None + family-tier None → existing theme-tier path."""
    jokes_enabled.set(conn, False)
    fake = _fake_song("theme-tier-song")

    def picker(seed: int, **kwargs: Any) -> Song | None:
        if kwargs.get("element_id") is not None:
            return None
        if kwargs.get("family_hint") is not None:
            return None
        if kwargs.get("theme") is Theme.space:
            return fake
        return None

    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[Theme.space],
        ),
        patch(
            "toybox.activities.content_resolver.pick_song",
            side_effect=picker,
        ),
        patch(
            "toybox.activities.content_resolver.family_for",
            return_value=Family.noble_gas,
        ),
    ):
        result = resolve_reward(conn, _ctx_with_element("ne-10"), "song")
    assert result is not None
    assert result.reward_id == "theme-tier-song"


def test_try_pick_song_no_element_id_uses_existing_theme_chain(
    conn: sqlite3.Connection,
) -> None:
    """Backwards compat: ctx.element_id=None → picker never sees element_id."""
    jokes_enabled.set(conn, False)
    fake = _fake_song("legacy-theme-song")
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[Theme.space],
        ),
        patch(
            "toybox.activities.content_resolver.pick_song",
            return_value=fake,
        ) as mock_pick,
    ):
        result = resolve_reward(conn, _ctx_with_element(None), "song")
    assert result is not None
    assert result.reward_id == "legacy-theme-song"
    for call in mock_pick.call_args_list:
        assert call.kwargs.get("element_id") is None
        assert call.kwargs.get("family_hint") is None


# ---------------------------------------------------------------------
# _try_pick_joke — element → family → theme chain (mirror of above)
# ---------------------------------------------------------------------


def test_try_pick_joke_passes_element_id_when_ctx_has_one(
    conn: sqlite3.Connection,
) -> None:
    """ctx.element_id set → pick_joke's FIRST call must carry element_id."""
    fake = _fake_joke("element-tier-joke")
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[Theme.silly],
        ),
        patch(
            "toybox.activities.content_resolver.pick_joke",
            return_value=fake,
        ) as mock_pick,
    ):
        result = resolve_reward(conn, _ctx_with_element("ne-10"), "joke")
    assert result is not None
    assert result.reward_id == "element-tier-joke"
    first_call = mock_pick.call_args_list[0]
    assert first_call.kwargs.get("element_id") == "ne-10"


def test_try_pick_joke_falls_back_to_family_when_element_no_match(
    conn: sqlite3.Connection,
) -> None:
    """Joke chain: element-tier None → family-tier with family_hint hit."""
    fake = _fake_joke("family-tier-joke")

    def picker(seed: int, **kwargs: Any) -> Joke | None:
        if kwargs.get("element_id") is not None:
            return None
        if kwargs.get("family_hint") is Family.noble_gas:
            return fake
        return None

    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[Theme.silly],
        ),
        patch(
            "toybox.activities.content_resolver.pick_joke",
            side_effect=picker,
        ) as mock_pick,
        patch(
            "toybox.activities.content_resolver.family_for",
            return_value=Family.noble_gas,
        ),
    ):
        result = resolve_reward(conn, _ctx_with_element("ne-10"), "joke")
    assert result is not None
    assert result.reward_id == "family-tier-joke"
    family_call = mock_pick.call_args_list[1]
    assert family_call.kwargs.get("family_hint") is Family.noble_gas


def test_try_pick_joke_falls_back_to_theme_when_neither_element_nor_family_match(
    conn: sqlite3.Connection,
) -> None:
    """Joke chain: element-tier None + family-tier None → theme-tier."""
    fake = _fake_joke("theme-tier-joke")

    def picker(seed: int, **kwargs: Any) -> Joke | None:
        if kwargs.get("element_id") is not None:
            return None
        if kwargs.get("family_hint") is not None:
            return None
        if kwargs.get("theme") is Theme.silly:
            return fake
        return None

    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[Theme.silly],
        ),
        patch(
            "toybox.activities.content_resolver.pick_joke",
            side_effect=picker,
        ),
        patch(
            "toybox.activities.content_resolver.family_for",
            return_value=Family.noble_gas,
        ),
    ):
        result = resolve_reward(conn, _ctx_with_element("ne-10"), "joke")
    assert result is not None
    assert result.reward_id == "theme-tier-joke"


def test_try_pick_joke_no_element_id_uses_existing_theme_chain(
    conn: sqlite3.Connection,
) -> None:
    """Backwards compat: pre-Q activities (element_id=None) → unchanged
    theme-tier behaviour.
    """
    fake = _fake_joke("legacy-theme-joke")
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[Theme.silly],
        ),
        patch(
            "toybox.activities.content_resolver.pick_joke",
            return_value=fake,
        ) as mock_pick,
    ):
        result = resolve_reward(conn, _ctx_with_element(None), "joke")
    assert result is not None
    assert result.reward_id == "legacy-theme-joke"
    for call in mock_pick.call_args_list:
        assert call.kwargs.get("element_id") is None
        assert call.kwargs.get("family_hint") is None


# ---------------------------------------------------------------------
# Wire-shape regression — Q5 must NOT change ResolvedReward
# ---------------------------------------------------------------------


def test_resolved_reward_wire_shape_unchanged_for_element_aware_picks(
    conn: sqlite3.Connection,
) -> None:
    """ResolvedReward shape is the SAME with element_id-aware picks
    as without. Phase Q Step Q5 must NOT change the wire envelope.
    """
    fake = _fake_song("shape-check-song")
    with (
        patch(
            "toybox.activities.content_resolver.extract_themes",
            return_value=[Theme.silly],
        ),
        patch(
            "toybox.activities.content_resolver.pick_song",
            return_value=fake,
        ),
    ):
        result = resolve_reward(conn, _ctx_with_element("ne-10"), "song")
    assert isinstance(result, ResolvedReward)
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
    }, f"unexpected fields: {field_names}"


# ---------------------------------------------------------------------
# RewardActivityContext extension — element_id default + identity
# ---------------------------------------------------------------------


def test_reward_activity_context_element_id_default_none() -> None:
    """The new element_id field is optional with default None — pre-Q
    callers can construct the context without naming it.
    """
    ctx = RewardActivityContext(
        id="act-1",
        session_id="sess-1",
        persona_id=None,
        slot_fills_json=None,
        current_step_count=0,
    )
    assert ctx.element_id is None


def test_reward_activity_context_element_id_kw_propagates() -> None:
    """When element_id is passed, it's stored verbatim on the context."""
    ctx = RewardActivityContext(
        id="act-1",
        session_id="sess-1",
        persona_id=None,
        slot_fills_json=None,
        current_step_count=0,
        element_id="ne-10",
    )
    assert ctx.element_id == "ne-10"


# ---------------------------------------------------------------------
# API caller exercise — _insert_reward_step_as_current extracts the
# primary element_id from the first persisted step row whose template-
# time step carries element_id, and threads it into the context.
# ---------------------------------------------------------------------


def test_api_reward_caller_extracts_primary_element_id_from_steps(
    conn: sqlite3.Connection,
) -> None:
    """Build an activity with steps where one carries element_id via the
    template lookup; assert that the resolve_reward call sees the right
    element_id on the RewardActivityContext.
    """
    from toybox.api.activities import (
        _insert_reward_step_as_current,
        _resolve_primary_element_id,
    )

    activity_id = "act-q5-1"
    template_id = "tpl-q5-elemented"
    with conn:
        conn.execute(
            "INSERT INTO activities "
            "(id, session_id, state, version, summary, reward_type, "
            " slot_fills_json, created_at) "
            "VALUES (?, 'sess-1', 'running', 1, ?, 'song', '{}', "
            " '2026-01-01T00:00:00Z')",
            (activity_id, json.dumps({"template_id": template_id})),
        )
        conn.execute(
            "INSERT INTO activity_steps "
            "(id, activity_id, seq, body, sfx, expected_action, current, "
            " action_slot, choices_json, step_template_id, kind, metadata_json) "
            "VALUES "
            "(?, ?, 1, 'opener body', NULL, NULL, 0, NULL, NULL, 'opener', 'text', NULL), "
            "(?, ?, 2, 'element body', NULL, NULL, 1, NULL, NULL, 'element_step', 'text', NULL)",
            (
                "step-1",
                activity_id,
                "step-2",
                activity_id,
            ),
        )

    # Build a fake template whose ``element_step`` carries element_id="ne-10".
    class _FakeStep:
        def __init__(self, sid: str, eid: str | None) -> None:
            self.id = sid
            self.element_id = eid

    class _FakeTemplate:
        steps = [_FakeStep("opener", None), _FakeStep("element_step", "ne-10")]

    with patch(
        "toybox.api.activities.find_template_by_id",
        return_value=_FakeTemplate(),
    ):
        # Direct helper exercise: resolves the primary element_id from
        # steps (ignores the step with no element_id, picks the first
        # whose template-time step has one).
        primary = _resolve_primary_element_id(
            conn, activity_id=activity_id, template_id=template_id
        )
        assert primary == "ne-10"

        # End-to-end: feed the activity row to the insert helper and
        # spy on the RewardActivityContext that resolve_reward sees.
        row = conn.execute(
            "SELECT * FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
        captured: dict[str, RewardActivityContext] = {}

        def _spy(_conn: sqlite3.Connection, ctx: RewardActivityContext, _kind: str) -> None:
            captured["ctx"] = ctx
            return None

        with patch("toybox.api.activities.resolve_reward", side_effect=_spy):
            appended = _insert_reward_step_as_current(conn, activity_id=activity_id, row=row)
        assert appended is False  # spy returned None
        assert captured["ctx"].element_id == "ne-10"


def test_api_reward_caller_threads_none_when_no_step_has_element_id(
    conn: sqlite3.Connection,
) -> None:
    """Backwards compat: an activity with no element-id step → context
    carries element_id=None and the legacy reward chain fires.
    """
    from toybox.api.activities import _insert_reward_step_as_current

    activity_id = "act-q5-2"
    template_id = "tpl-q5-no-element"
    with conn:
        conn.execute(
            "INSERT INTO activities "
            "(id, session_id, state, version, summary, reward_type, "
            " slot_fills_json, created_at) "
            "VALUES (?, 'sess-1', 'running', 1, ?, 'song', '{}', "
            " '2026-01-01T00:00:00Z')",
            (activity_id, json.dumps({"template_id": template_id})),
        )
        conn.execute(
            "INSERT INTO activity_steps "
            "(id, activity_id, seq, body, sfx, expected_action, current, "
            " action_slot, choices_json, step_template_id, kind, metadata_json) "
            "VALUES "
            "(?, ?, 1, 'body', NULL, NULL, 1, NULL, NULL, 'opener', 'text', NULL)",
            ("step-1", activity_id),
        )

    class _FakeStep:
        def __init__(self, sid: str, eid: str | None) -> None:
            self.id = sid
            self.element_id = eid

    class _FakeTemplate:
        steps = [_FakeStep("opener", None)]

    with patch(
        "toybox.api.activities.find_template_by_id",
        return_value=_FakeTemplate(),
    ):
        row = conn.execute(
            "SELECT * FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
        captured: dict[str, RewardActivityContext] = {}

        def _spy(_conn: sqlite3.Connection, ctx: RewardActivityContext, _kind: str) -> None:
            captured["ctx"] = ctx
            return None

        with patch("toybox.api.activities.resolve_reward", side_effect=_spy):
            _insert_reward_step_as_current(conn, activity_id=activity_id, row=row)
        assert captured["ctx"].element_id is None
