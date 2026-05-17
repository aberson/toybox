"""Phase K Step K15 — Surface P (parent-insert) integration tests.

The K15 plan introduced two active interjection surfaces:

* **P — Parent-insert.** ``POST /api/activities/{id}/insert-joke`` +
  ``POST /api/activities/{id}/insert-song`` insert a parent-driven
  interjection at ``current_step + 1`` on a running/paused activity.
* **S — Spontaneity advance hook.** Deleted in Phase L Step L5 when
  jokes/songs migrated to per-activity reward types. The integration
  tests that exercised Surface S were removed alongside the code.

Tests below exercise Surface P end-to-end through the production
``POST /propose`` → ``POST /approve`` → ``POST /advance`` chain
(code-quality.md §4: new components need an integration test through
the production caller).

Per-test isolation:

* ``_stage_templates`` writes a tmp ``boredom.json`` + the other
  three production intents so the propose dispatcher lands on the
  fixture.
* ``k15_corpus`` patches the song + joke corpora to single-entry
  stubs so the picker's deterministic ``seed % len(candidates)``
  always lands on the known id.
* ``_set_flag`` writes the per-test SQLite settings rows directly
  so the K2 feature-flag readers see the desired master / surface
  state.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.activities import joke_corpus, song_corpus
from toybox.activities.generator import TEMPLATES_DIR, clear_template_cache
from toybox.activities.interjections import InterjectionKind
from toybox.db.connection import connect

# ---------------------------------------------------------------------
# Corpus + persona fixtures
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


def _good_song_entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "k15-stub-song",
        "title": "K15 Stub Song",
        "audio_path": "audio/k15-stub-song.mp3",
        "duration_seconds": 10,
        "theme": "adventure",
        "age_band": "3-5",
        "persona_compat": ["all"],
        "license": "CC-BY-4.0",
        "credit": "K15 test fixture",
        "lyrics": "Tra la la la.",
    }
    base.update(overrides)
    return base


def _good_joke_entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "k15-stub-joke",
        "setup": "Why did the K15 button click?",
        "punchline": "To insert a joke.",
        "theme": "silly",
        "optional_toy_slot": False,
        "age_band": "3-5",
        "persona_compat": ["all"],
    }
    base.update(overrides)
    return base


# Minimal three-step template with no role placeholders. Used by tests
# that only need an in-flight activity for the parent-insert endpoints.
_TEMPLATE_PLAIN_3STEP: dict[str, Any] = {
    "intent": "boredom",
    "templates": [
        {
            "id": "k15_plain_3step",
            "title": "Plain three-step activity",
            "buckets": ["always"],
            "steps": [
                {"text": "First step."},
                {"text": "Second step."},
                {"text": "Third step."},
            ],
        }
    ],
}


def _stage_templates(tmp_path: Path, boredom_payload: dict[str, Any]) -> Path:
    staged = tmp_path / "templates_k15"
    staged.mkdir()
    shutil.copy(TEMPLATES_DIR / "_schema.json", staged / "_schema.json")
    (staged / "boredom.json").write_text(json.dumps(boredom_payload), encoding="utf-8")
    for intent in ("request_play", "request_story", "request_activity"):
        shutil.copy(TEMPLATES_DIR / f"{intent}.json", staged / f"{intent}.json")
    return staged


@pytest.fixture
def k15_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Point joke + song corpora at single-entry tmp fixtures."""
    _write_song_manifest(tmp_path, [_good_song_entry()])
    _stub_audio(tmp_path, "audio/k15-stub-song.mp3")
    _write_joke_corpus(tmp_path, [_good_joke_entry()])
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    song_corpus.clear_song_cache()
    joke_corpus.clear_joke_cache()
    try:
        yield tmp_path
    finally:
        song_corpus.clear_song_cache()
        joke_corpus.clear_joke_cache()


# ---------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------


def _set_flag(db_path: Path, key: str, value: bool) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, "true" if value else "false"),
            )
    finally:
        conn.close()


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
) -> dict[str, Any]:
    response = client.post(
        f"/api/activities/{activity_id}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert response.status_code == 200, response.text
    return cast("dict[str, Any]", response.json())


def _advance(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    version: int,
) -> dict[str, Any]:
    response = client.post(
        f"/api/activities/{activity_id}/advance",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert response.status_code == 200, response.text
    return cast("dict[str, Any]", response.json())


def _walk_to_running_at_step_1(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    initial_version: int,
) -> dict[str, Any]:
    """approve + first advance → activity is running with seq=1 current."""
    state = _approve(client, parent_headers, activity_id, initial_version)
    state = _advance(client, parent_headers, activity_id, int(state["version"]))
    assert state["state"] == "running"
    return state


def _seed_role_weighted_persona(db_path: Path) -> None:
    """Insert a persona with non-zero spontaneity_rates so K15 tests
    that exercise the persona-side rate have something to read.
    """
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO personas "
                "(id, display_name, archetype, system_prompt, avatar_image_path, "
                " behavior_tags, age_range_min, age_range_max, language, source, "
                " default_voice_tone, created_at, role_weights, voice_profile, "
                " spontaneity_rates) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "k15_persona",
                    "K15 Persona",
                    "custom",
                    "Test persona for K15.",
                    "library/avatars/k15.png",
                    json.dumps(["test"]),
                    3,
                    10,
                    "en",
                    "library",
                    "neutral",
                    "2026-05-15T00:00:00Z",
                    "{}",
                    None,
                    json.dumps({"jokes": 0.0, "songs": 0.0}, sort_keys=True),
                ),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Surface P — parent-insert happy paths
# ---------------------------------------------------------------------


def test_insert_joke_happy_path_inserts_step_bumps_version(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k15_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent-insert joke on running activity: new ``activity_steps``
    row at seq=2 with ``kind="joke"`` + ``metadata.interjection="parent"``,
    version bumps by exactly 1, response carries the parent step in
    ``steps[]``.
    """
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _walk_to_running_at_step_1(
        client,
        parent_headers,
        activity_id,
        int(activity["version"]),
    )
    pre_version = int(state["version"])

    resp = client.post(
        f"/api/activities/{activity_id}/insert-joke",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(pre_version)},
    )
    assert resp.status_code == 200, resp.text
    body = cast("dict[str, Any]", resp.json())

    assert body["version"] == pre_version + 1, "insert must bump version once"
    assert body["state"] == "running", "insert preserves state"
    # interjection_pending only fires for spontaneity rolls; parent
    # insert is an explicit action and intentionally leaves it false.
    assert body.get("interjection_pending") in (False, None)

    rows = _fetch_steps(db_path, activity_id)
    assert [r["seq"] for r in rows] == [1, 2]
    inserted = rows[1]
    assert inserted["kind"] == "joke"
    assert inserted["current"] is True, "the inserted joke should become current"
    assert inserted["body"], "joke body must be non-empty"
    meta = json.loads(inserted["metadata_json"])
    assert meta["interjection"] == InterjectionKind.parent.value
    assert meta["source_id"] == "k15-stub-joke"
    assert meta["joke_id"] == "k15-stub-joke"
    assert isinstance(meta["punchline"], str) and meta["punchline"]


def test_insert_song_happy_path_inserts_step_bumps_version(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k15_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent-insert song happy path: new step at seq=2 with
    ``kind="song"`` + ``metadata.interjection="parent"`` + audio_url.
    """
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _walk_to_running_at_step_1(
        client,
        parent_headers,
        activity_id,
        int(activity["version"]),
    )
    pre_version = int(state["version"])

    resp = client.post(
        f"/api/activities/{activity_id}/insert-song",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(pre_version)},
    )
    assert resp.status_code == 200, resp.text
    body = cast("dict[str, Any]", resp.json())
    assert body["version"] == pre_version + 1

    rows = _fetch_steps(db_path, activity_id)
    assert [r["seq"] for r in rows] == [1, 2]
    inserted = rows[1]
    assert inserted["kind"] == "song"
    meta = json.loads(inserted["metadata_json"])
    assert meta["interjection"] == InterjectionKind.parent.value
    assert meta["source_id"] == "k15-stub-song"
    assert meta["song_id"] == "k15-stub-song"
    assert meta["audio_url"].endswith("/api/static/songs/audio/k15-stub-song.mp3")


def test_insert_logs_labeled_events_tool_call(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k15_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The insert endpoint appends an interjection event to the
    activity's ``labeled_events.tool_calls`` JSON column with
    ``source = "parent_insert"`` (plan §6 K15 telemetry).
    """
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _walk_to_running_at_step_1(
        client,
        parent_headers,
        activity_id,
        int(activity["version"]),
    )

    resp = client.post(
        f"/api/activities/{activity_id}/insert-joke",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert resp.status_code == 200, resp.text

    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT tool_calls FROM labeled_events WHERE activity_id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "labeled_events row must exist (propose recorded one)"
    assert row["tool_calls"] is not None, "tool_calls must be populated by parent-insert"
    entries = json.loads(row["tool_calls"])
    assert isinstance(entries, list)
    interjection_entries = [e for e in entries if e.get("event") == "interjection"]
    assert len(interjection_entries) >= 1
    entry = interjection_entries[-1]
    assert entry["source"] == "parent_insert"
    assert entry["interjection_kind"] == InterjectionKind.parent.value
    assert entry["corpus_entry_id"] == "k15-stub-joke"
    assert entry["step_seq"] == 2


# ---------------------------------------------------------------------
# Surface P — state guards
# ---------------------------------------------------------------------


def test_insert_joke_on_proposed_activity_returns_409(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k15_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proposed activities reject insert with 409
    ``insert_only_when_running_or_paused``.
    """
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()

    activity = _propose(client, parent_headers)
    resp = client.post(
        f"/api/activities/{activity['id']}/insert-joke",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(activity["version"])},
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "insert_only_when_running_or_paused"
    assert detail["current_state"] == "proposed"


def test_insert_joke_with_content_master_off_returns_409(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k15_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``jokes_enabled = false`` triggers 409 ``content_disabled``."""
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()
    _set_flag(db_path, "jokes_enabled", False)

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _walk_to_running_at_step_1(
        client,
        parent_headers,
        activity_id,
        int(activity["version"]),
    )

    resp = client.post(
        f"/api/activities/{activity_id}/insert-joke",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "content_disabled"
    assert detail["kind"] == "joke"


def test_insert_song_with_content_master_off_returns_409(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k15_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``songs_enabled = false`` triggers 409 ``content_disabled``."""
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()
    _set_flag(db_path, "songs_enabled", False)

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _walk_to_running_at_step_1(
        client,
        parent_headers,
        activity_id,
        int(activity["version"]),
    )

    resp = client.post(
        f"/api/activities/{activity_id}/insert-song",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "content_disabled"
    assert detail["kind"] == "song"


def test_insert_joke_with_stale_version_returns_409_version_conflict(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k15_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale ``If-Match-Version`` returns standard 409 ``version_conflict``."""
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _walk_to_running_at_step_1(
        client,
        parent_headers,
        activity_id,
        int(activity["version"]),
    )

    stale_version = int(state["version"]) - 1
    resp = client.post(
        f"/api/activities/{activity_id}/insert-joke",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(stale_version)},
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "version_conflict"


def test_insert_joke_on_paused_state_allowed(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k15_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paused activities accept parent-insert (plan §6 K15: ``running``
    or ``paused`` allowed).
    """
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    state = _walk_to_running_at_step_1(
        client,
        parent_headers,
        activity_id,
        int(activity["version"]),
    )

    # Pause the activity.
    pause_resp = client.post(
        f"/api/activities/{activity_id}/pause",
        json={},
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert pause_resp.status_code == 200, pause_resp.text
    pause_body = pause_resp.json()

    # Insert joke while paused.
    resp = client.post(
        f"/api/activities/{activity_id}/insert-joke",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(pause_body["version"])},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "paused", "state stays paused after insert"


