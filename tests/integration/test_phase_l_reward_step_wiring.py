"""Phase L Step L4 — reward step wired into the production advance handler.

Per code-quality.md §4 (new components require an integration test
through the production caller): L4 wires the L3-shipped
:func:`resolve_reward` into :func:`_terminal_advance` so the kiosk
sees a ``kind="reward"`` step appended at activity end.

Tests below exercise the production wire path
(``POST /api/activities/propose`` → ``approve`` → ``advance`` × N)
end-to-end, not the unit-level resolver (covered in L3's unit suite).
Each test focuses on ONE assertion to keep failures unambiguous.

Coverage matrix:

* One full cycle for each of ``picture | joke | song | random``.
* Fallback chain: no picture rewards + jokes_enabled → joke fires.
* Total empty pool (no rewards + jokes off + songs off) → no reward
  step, activity completes cleanly.
* ``rewards.last_used_at`` is updated for picture rewards.
* Pre-L legacy rows (``reward_type`` NULL) → no reward step appended.
* ``__template_id`` is written into ``slot_fills_json`` on approve.
* Idempotency: a second advance after terminal does NOT double-insert
  (the ``_has_reward_step`` guard + state-transition wedge).
* Version bump: the reward step insertion bumps ``activities.version``
  by one (so concurrent clients see the conflict).
* WS envelope: subscribing to ``activity.state`` and walking to
  terminal yields an envelope carrying the reward step + has
  ``trigger_phrase`` stripped per invariant 7.
"""

from __future__ import annotations

import io
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from toybox.activities import joke_corpus, song_corpus
from toybox.activities.generator import TEMPLATES_DIR, clear_template_cache
from toybox.core import jokes_enabled, songs_enabled
from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
from toybox.ws.topics import Topic

# ---------------------------------------------------------------------
# Corpus + template fixtures — single-entry corpora keep the picker
# deterministic; the template advertises the "adventure" theme so the
# rewards tagged "adventure" win the L3 tag-match.
# ---------------------------------------------------------------------


def _write_song_manifest(data_root: Path, entries: list[dict[str, Any]]) -> None:
    songs_dir = data_root / "songs"
    songs_dir.mkdir(parents=True, exist_ok=True)
    (songs_dir / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")


def _write_joke_corpus(data_root: Path, entries: list[dict[str, Any]]) -> None:
    jokes_dir = data_root / "jokes"
    jokes_dir.mkdir(parents=True, exist_ok=True)
    (jokes_dir / "jokes.json").write_text(json.dumps(entries), encoding="utf-8")


def _stub_audio(data_root: Path, audio_path: str) -> Path:
    full = data_root / "songs" / audio_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(b"\x00" * 32)
    return full


def _good_song_entry() -> dict[str, Any]:
    return {
        "id": "l4-stub-song",
        "title": "L4 Stub Song",
        "audio_path": "audio/l4-stub-song.mp3",
        "duration_seconds": 10,
        "theme": "adventure",
        "age_band": "3-5",
        "persona_compat": ["all"],
        "license": "CC-BY-4.0",
        "credit": "L4 test fixture",
        "lyrics": "La la la.",
    }


def _good_joke_entry() -> dict[str, Any]:
    return {
        "id": "l4-stub-joke",
        "setup": "Why did the L4 chicken cross the road?",
        "punchline": "To reach the reward step.",
        "theme": "adventure",
        "optional_toy_slot": False,
        "age_band": "3-5",
        "persona_compat": ["all"],
    }


# Minimal three-step template. The kid walks through steps 1, 2, 3 and
# the fourth advance is the terminal one that fires the reward.
_TEMPLATE_BOREDOM_THREE_STEP: dict[str, Any] = {
    "intent": "boredom",
    "templates": [
        {
            "id": "l4_three_step",
            "title": "An adventure quest",
            "buckets": ["always"],
            "recommended_themes": ["adventure"],
            "steps": [
                {"text": "Step one of the adventure."},
                {"text": "Step two of the adventure."},
                {"text": "Step three of the adventure."},
            ],
        }
    ],
}


def _stage_templates(tmp_path: Path, boredom_payload: dict[str, Any]) -> Path:
    """Stage a tmp templates dir with a custom ``boredom.json`` and
    the other three production intents copied unchanged. The path is
    monkeypatched into ``generator.TEMPLATES_DIR`` by the caller so
    the propose handler lands on the staged template.
    """
    staged = tmp_path / "templates_l4"
    staged.mkdir()
    shutil.copy(TEMPLATES_DIR / "_schema.json", staged / "_schema.json")
    (staged / "boredom.json").write_text(json.dumps(boredom_payload), encoding="utf-8")
    for intent in ("request_play", "request_story", "request_activity"):
        shutil.copy(TEMPLATES_DIR / f"{intent}.json", staged / f"{intent}.json")
    return staged


@pytest.fixture
def l4_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Point joke + song corpora at single-entry tmp fixtures so the
    L3 picker always lands on the known stub id when a tag matches.
    """
    _write_song_manifest(tmp_path, [_good_song_entry()])
    _stub_audio(tmp_path, "audio/l4-stub-song.mp3")
    _write_joke_corpus(tmp_path, [_good_joke_entry()])
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    song_corpus.clear_song_cache()
    joke_corpus.clear_joke_cache()
    try:
        yield tmp_path
    finally:
        song_corpus.clear_song_cache()
        joke_corpus.clear_joke_cache()


@pytest.fixture
def l4_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Stage the three-step boredom template at the generator's
    TEMPLATES_DIR pointer."""
    staged = _stage_templates(tmp_path, _TEMPLATE_BOREDOM_THREE_STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()
    return staged


# ---------------------------------------------------------------------
# REST helpers (mirror the embedded/endings test pattern).
# ---------------------------------------------------------------------


def _propose(
    client: TestClient,
    parent_headers: dict[str, str],
    *,
    seed: int = 17,
) -> dict[str, Any]:
    response = client.post(
        "/api/activities/propose",
        json={"intent": "boredom", "slot": None, "hour": 12, "seed": seed},
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


def _approve(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    version: int,
    *,
    reward_type: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if reward_type is not None:
        body["reward_type"] = reward_type
    response = client.post(
        f"/api/activities/{activity_id}/approve",
        json=body,
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert response.status_code == 200, response.text
    return cast("dict[str, Any]", response.json())


def _advance(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    version: int,
    *,
    expected_status: int = 200,
) -> dict[str, Any]:
    response = client.post(
        f"/api/activities/{activity_id}/advance",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert response.status_code == expected_status, response.text
    return cast("dict[str, Any]", response.json())


def _walk_to_terminal(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    starting_version: int,
) -> dict[str, Any]:
    """Walk the three-step boredom template to completion.

    Sequence after ``approve``:

    1. advance: approved → running on seq=1 (no INSERT — seq=1 is
       pre-seeded by ``_persist_activity``).
    2. advance: lazy-INSERT seq=2 (template step 2).
    3. advance: lazy-INSERT seq=3 (template step 3).
    4. advance: terminal — state → completed; L4 appends reward step
       (if eligible).
    """
    version = starting_version
    state = _advance(client, parent_headers, activity_id, version)
    version = int(state["version"])
    state = _advance(client, parent_headers, activity_id, version)
    version = int(state["version"])
    state = _advance(client, parent_headers, activity_id, version)
    version = int(state["version"])
    state = _advance(client, parent_headers, activity_id, version)
    return state


def _png_bytes() -> bytes:
    img = Image.new("RGB", (32, 32), (200, 50, 50))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _create_reward(
    client: TestClient,
    parent_headers: dict[str, str],
    *,
    display_name: str = "Treasure Chest",
    tags: list[str] | None = None,
    animation: str = "shine",
) -> dict[str, Any]:
    """Upload + confirm one reward."""
    upload_resp = client.post(
        "/api/rewards/upload",
        files={"file": ("reward.png", _png_bytes(), "image/png")},
        headers=parent_headers,
    )
    assert upload_resp.status_code == 200, upload_resp.text
    staging_key = upload_resp.json()["staging_key"]
    confirm_resp = client.post(
        "/api/rewards",
        json={
            "staging_key": staging_key,
            "display_name": display_name,
            "tags": tags if tags is not None else ["adventure"],
            "animation": animation,
            "active": True,
        },
        headers=parent_headers,
    )
    assert confirm_resp.status_code == 201, confirm_resp.text
    return cast("dict[str, Any]", confirm_resp.json())


def _fetch_steps(db_path: Path, activity_id: str) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT seq, body, kind, metadata_json, current "
            "FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC",
            (activity_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "seq": int(r["seq"]),
            "body": str(r["body"]),
            "kind": r["kind"],
            "metadata_json": r["metadata_json"],
            "current": bool(r["current"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------
# Cycle: explicit reward types — picture, joke, song.
# ---------------------------------------------------------------------


def test_picture_reward_cycle_fires_and_metadata_is_shaped(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l4_corpus: Path,
    l4_template: Path,
) -> None:
    """``reward_type='picture'`` with one tagged reward → reward step
    appended at terminal with ``reward_kind='picture'`` + picture-only
    fields populated per plan §8."""
    reward = _create_reward(client, parent_headers)
    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _approve(
        client, parent_headers, activity_id, activity["version"], reward_type="picture"
    )
    state = _walk_to_terminal(client, parent_headers, activity_id, state["version"])

    assert state["state"] == "completed"
    rows = _fetch_steps(db_path, activity_id)
    reward_rows = [r for r in rows if r["kind"] == "reward"]
    assert len(reward_rows) == 1, f"expected exactly 1 reward step, got {len(reward_rows)}"
    meta = json.loads(reward_rows[0]["metadata_json"])
    assert meta["reward_kind"] == "picture"
    assert meta["reward_id"] == reward["id"]
    assert meta["image_url"] is not None
    assert meta["animation"] == "shine"
    # Per-kind exclusivity: picture rewards have null audio/setup/punchline.
    assert meta["audio_url"] is None
    assert meta["setup"] is None
    assert meta["punchline"] is None
    assert meta["body"] == reward["display_name"]


def test_joke_reward_cycle_fires_with_setup_and_punchline(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l4_corpus: Path,
    l4_template: Path,
) -> None:
    """``reward_type='joke'`` with the stub joke corpus → reward step
    has ``reward_kind='joke'`` + setup + punchline populated."""
    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _approve(client, parent_headers, activity_id, activity["version"], reward_type="joke")
    state = _walk_to_terminal(client, parent_headers, activity_id, state["version"])

    assert state["state"] == "completed"
    rows = _fetch_steps(db_path, activity_id)
    reward_rows = [r for r in rows if r["kind"] == "reward"]
    assert len(reward_rows) == 1
    meta = json.loads(reward_rows[0]["metadata_json"])
    assert meta["reward_kind"] == "joke"
    assert meta["reward_id"] == "l4-stub-joke"
    assert meta["setup"] == "Why did the L4 chicken cross the road?"
    assert meta["punchline"] == "To reach the reward step."
    # Joke rewards have null image/animation/audio.
    assert meta["image_url"] is None
    assert meta["animation"] is None
    assert meta["audio_url"] is None


def test_song_reward_cycle_fires_with_audio_url(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l4_corpus: Path,
    l4_template: Path,
) -> None:
    """``reward_type='song'`` with the stub song corpus → reward step
    has ``reward_kind='song'`` + audio_url populated."""
    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _approve(client, parent_headers, activity_id, activity["version"], reward_type="song")
    state = _walk_to_terminal(client, parent_headers, activity_id, state["version"])

    assert state["state"] == "completed"
    rows = _fetch_steps(db_path, activity_id)
    reward_rows = [r for r in rows if r["kind"] == "reward"]
    assert len(reward_rows) == 1
    meta = json.loads(reward_rows[0]["metadata_json"])
    assert meta["reward_kind"] == "song"
    assert meta["reward_id"] == "l4-stub-song"
    assert meta["audio_url"] == "/api/static/songs/audio/l4-stub-song.mp3"
    assert meta["body"] == "L4 Stub Song"
    # Song rewards have null image/animation/setup/punchline.
    assert meta["image_url"] is None
    assert meta["animation"] is None
    assert meta["setup"] is None
    assert meta["punchline"] is None


# ---------------------------------------------------------------------
# Random: must land on one of the three kinds.
# ---------------------------------------------------------------------


def test_random_reward_cycle_fires_one_of_three_kinds(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l4_corpus: Path,
    l4_template: Path,
) -> None:
    """``reward_type='random'`` with all three pools populated → reward
    step has ``reward_kind`` in {picture, joke, song}."""
    _create_reward(client, parent_headers)
    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _approve(client, parent_headers, activity_id, activity["version"], reward_type="random")
    state = _walk_to_terminal(client, parent_headers, activity_id, state["version"])

    rows = _fetch_steps(db_path, activity_id)
    reward_rows = [r for r in rows if r["kind"] == "reward"]
    assert len(reward_rows) == 1
    meta = json.loads(reward_rows[0]["metadata_json"])
    assert meta["reward_kind"] in ("picture", "joke", "song")


# ---------------------------------------------------------------------
# Fallback chain.
# ---------------------------------------------------------------------


def test_picture_fallback_to_joke_when_no_active_rewards(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l4_corpus: Path,
    l4_template: Path,
) -> None:
    """No active picture rewards in DB + jokes_enabled → ``reward_type=
    'picture'`` falls through to ``joke`` per plan §3 chain order."""
    # No reward created; jokes_enabled defaults to True; songs come after
    # joke in the fallback order so joke should win.
    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _approve(
        client, parent_headers, activity_id, activity["version"], reward_type="picture"
    )
    state = _walk_to_terminal(client, parent_headers, activity_id, state["version"])

    rows = _fetch_steps(db_path, activity_id)
    reward_rows = [r for r in rows if r["kind"] == "reward"]
    assert len(reward_rows) == 1
    meta = json.loads(reward_rows[0]["metadata_json"])
    assert meta["reward_kind"] == "joke", (
        f"expected joke fallback when no pictures exist, got {meta['reward_kind']!r}"
    )


def test_no_reward_step_when_all_pools_empty(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l4_template: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No pictures + jokes_enabled=False + songs_enabled=False →
    activity completes cleanly without a reward step appended."""
    # Disable both corpora at the household-flag level. Note: we
    # intentionally don't point at corpus stubs — even without flag
    # gating, an empty corpus would suffice, but the flag is the
    # cleaner contract.
    conn = connect(db_path)
    try:
        jokes_enabled.set(conn, False)
        songs_enabled.set(conn, False)
    finally:
        conn.close()
    # Also force empty corpora as belt-and-suspenders so the random
    # roll doesn't pick a type whose corpus we forgot to bound.
    _write_song_manifest(tmp_path, [])
    _write_joke_corpus(tmp_path, [])
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    song_corpus.clear_song_cache()
    joke_corpus.clear_joke_cache()
    try:
        activity = _propose(client, parent_headers)
        activity_id = activity["id"]
        state = _approve(
            client,
            parent_headers,
            activity_id,
            activity["version"],
            reward_type="picture",
        )
        state = _walk_to_terminal(client, parent_headers, activity_id, state["version"])
        assert state["state"] == "completed"
        rows = _fetch_steps(db_path, activity_id)
        reward_rows = [r for r in rows if r["kind"] == "reward"]
        assert reward_rows == [], f"no reward step expected when all pools empty; got {reward_rows}"
    finally:
        song_corpus.clear_song_cache()
        joke_corpus.clear_joke_cache()


# ---------------------------------------------------------------------
# rewards.last_used_at — picture rewards only.
# ---------------------------------------------------------------------


def test_picture_reward_updates_last_used_at(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l4_corpus: Path,
    l4_template: Path,
) -> None:
    """A picture reward firing → ``rewards.last_used_at`` flips from
    NULL to an ISO timestamp."""
    reward = _create_reward(client, parent_headers)
    # Sanity: last_used_at starts NULL.
    conn = connect(db_path)
    try:
        before = conn.execute(
            "SELECT last_used_at FROM rewards WHERE id = ?",
            (reward["id"],),
        ).fetchone()
        assert before["last_used_at"] is None
    finally:
        conn.close()

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _approve(
        client, parent_headers, activity_id, activity["version"], reward_type="picture"
    )
    _walk_to_terminal(client, parent_headers, activity_id, state["version"])

    conn = connect(db_path)
    try:
        after = conn.execute(
            "SELECT last_used_at FROM rewards WHERE id = ?",
            (reward["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert after["last_used_at"] is not None
    # ISO-8601 UTC shape: "YYYY-MM-DDTHH:MM:SSZ" — pin the suffix so
    # a future tz drift surfaces here.
    assert str(after["last_used_at"]).endswith("Z"), (
        f"last_used_at not in expected ISO-Z shape: {after['last_used_at']!r}"
    )


# ---------------------------------------------------------------------
# Pre-L legacy: reward_type NULL must not append a reward step.
# ---------------------------------------------------------------------


def test_pre_l_activity_with_null_reward_type_appends_no_reward(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l4_corpus: Path,
    l4_template: Path,
) -> None:
    """An activity row with NULL ``reward_type`` (the pre-L legacy
    shape) must NOT append a reward step. Tests the explicit guard in
    :func:`_maybe_append_reward_step`."""
    # Have pictures + corpora available so a non-NULL reward_type would
    # definitely fire — the only thing stopping the reward is the NULL
    # column.
    _create_reward(client, parent_headers)
    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _approve(
        client, parent_headers, activity_id, activity["version"], reward_type="picture"
    )
    # Forcibly NULL out the reward_type column to simulate a pre-L row.
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE activities SET reward_type = NULL WHERE id = ?",
                (activity_id,),
            )
    finally:
        conn.close()

    state = _walk_to_terminal(client, parent_headers, activity_id, state["version"])
    assert state["state"] == "completed"
    rows = _fetch_steps(db_path, activity_id)
    reward_rows = [r for r in rows if r["kind"] == "reward"]
    assert reward_rows == [], (
        f"pre-L NULL reward_type must not append a reward step; got {reward_rows}"
    )
    # Wire shape: ``reward_type`` surfaces as ``None`` on the response
    # (NOT coerced to "random") per plan §2.
    assert state["reward_type"] is None


# ---------------------------------------------------------------------
# Approve-time persistence: __template_id written into slot_fills_json.
# ---------------------------------------------------------------------


def test_approve_writes_template_id_into_slot_fills_json(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l4_corpus: Path,
    l4_template: Path,
) -> None:
    """The approve handler MUST write ``__template_id`` into
    ``slot_fills_json`` so the L3 resolver can look up the template's
    ``recommended_themes``."""
    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    _approve(client, parent_headers, activity_id, activity["version"], reward_type="random")

    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT slot_fills_json FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    slot_fills = json.loads(row["slot_fills_json"])
    assert "__template_id" in slot_fills, (
        f"approve must write __template_id into slot_fills_json; got keys {list(slot_fills.keys())}"
    )
    assert slot_fills["__template_id"] == "l4_three_step"


# ---------------------------------------------------------------------
# Idempotency: no double-insert when advancing past terminal.
# ---------------------------------------------------------------------


def test_advance_past_terminal_does_not_insert_second_reward_step(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l4_corpus: Path,
    l4_template: Path,
) -> None:
    """The state-transition wedge (completed→advance is 409) already
    prevents a re-fire, but pin the contract that even if a caller
    contrives to call ``_terminal_advance`` twice, only one reward
    step lands. We exercise this via the natural REST path: an extra
    advance after completed returns 409 and the step count is
    unchanged."""
    _create_reward(client, parent_headers)
    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _approve(
        client, parent_headers, activity_id, activity["version"], reward_type="picture"
    )
    state = _walk_to_terminal(client, parent_headers, activity_id, state["version"])
    assert state["state"] == "completed"
    rows_before = _fetch_steps(db_path, activity_id)
    reward_count_before = sum(1 for r in rows_before if r["kind"] == "reward")
    assert reward_count_before == 1

    # Another advance attempt is rejected as invalid transition (409)
    # — the wedge that gives us idempotency for free.
    _advance(
        client,
        parent_headers,
        activity_id,
        int(state["version"]),
        expected_status=409,
    )
    rows_after = _fetch_steps(db_path, activity_id)
    reward_count_after = sum(1 for r in rows_after if r["kind"] == "reward")
    assert reward_count_after == 1, (
        f"reward step must not double-insert across a post-terminal advance; "
        f"before={reward_count_before} after={reward_count_after}"
    )


# ---------------------------------------------------------------------
# Version bump: the reward step insertion bumps version by one.
# ---------------------------------------------------------------------


def test_reward_step_insertion_bumps_version(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    l4_corpus: Path,
    l4_template: Path,
) -> None:
    """The reward step append bumps ``activities.version`` by 1 (on top
    of the +1 the state transition already applied). Captured here so
    a future refactor that drops the version bump surfaces the
    optimistic-concurrency regression at test time, not at parent-UI
    "409 mystery" time."""
    _create_reward(client, parent_headers)
    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _approve(
        client, parent_headers, activity_id, activity["version"], reward_type="picture"
    )
    # Walk to the LAST step before terminal so we know the version
    # immediately pre-terminal-advance.
    version = int(state["version"])
    state = _advance(client, parent_headers, activity_id, version)  # → running on seq 1
    version = int(state["version"])
    state = _advance(client, parent_headers, activity_id, version)  # → seq 2
    version = int(state["version"])
    state = _advance(client, parent_headers, activity_id, version)  # → seq 3
    pre_terminal_version = int(state["version"])

    # The terminal advance: state→completed (+1) + reward step append (+1).
    state = _advance(client, parent_headers, activity_id, pre_terminal_version)
    assert state["state"] == "completed"
    assert state["version"] == pre_terminal_version + 2, (
        f"expected version to jump by 2 (state transition + reward step), got "
        f"{state['version']} from pre-terminal {pre_terminal_version}"
    )


# ---------------------------------------------------------------------
# WS envelope: terminal advance publishes a state envelope carrying
# the reward step + strips trigger_phrase.
# ---------------------------------------------------------------------


def test_terminal_advance_publishes_reward_step_envelope_and_strips_trigger_phrase(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    pubsub: PubSub,
    l4_corpus: Path,
    l4_template: Path,
) -> None:
    """Subscribe to ``activity.state``, walk to terminal, drain the
    queue, and pin two contracts:

    1. The LAST envelope on the topic carries the reward step in its
       ``payload.steps`` array.
    2. ``trigger_phrase`` is NOT present on any envelope payload (per
       ``_emit_state`` invariant 7).
    """
    _create_reward(client, parent_headers)
    sub = pubsub.subscribe([Topic.activity_state])
    try:
        activity = _propose(client, parent_headers)
        activity_id = activity["id"]
        state = _approve(
            client,
            parent_headers,
            activity_id,
            activity["version"],
            reward_type="picture",
        )
        state = _walk_to_terminal(client, parent_headers, activity_id, state["version"])

        # Drain the queue and inspect the last envelope on this topic.
        seen_envelopes: list[Any] = []
        while True:
            try:
                seen_envelopes.append(sub.get_nowait())
            except Exception:
                break
        assert seen_envelopes, "expected at least one activity.state envelope"
        last = seen_envelopes[-1]
        assert last.topic is Topic.activity_state
        assert last.payload["id"] == activity_id
        # The reward step is in the envelope's steps list.
        steps = last.payload["steps"]
        reward_steps = [s for s in steps if s.get("kind") == "reward"]
        assert len(reward_steps) == 1, (
            f"envelope must carry the reward step; got steps with kinds "
            f"{[s.get('kind') for s in steps]}"
        )
        # Every envelope on this topic has trigger_phrase stripped per
        # invariant 7.
        for env in seen_envelopes:
            assert "trigger_phrase" not in env.payload, (
                f"trigger_phrase leaked onto activity.state envelope: {env.payload}"
            )
    finally:
        sub.close()
