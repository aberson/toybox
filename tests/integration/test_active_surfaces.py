"""Phase K Step K15 — active interjection surfaces (P + S) integration tests.

Two new content-delivery surfaces:

* **P — Parent-insert.** ``POST /api/activities/{id}/insert-joke`` +
  ``POST /api/activities/{id}/insert-song`` insert a parent-driven
  interjection at ``current_step + 1`` on a running/paused activity.
* **S — Spontaneity advance hook.** During ``POST /advance`` the engine
  rolls a per-content-type ``effective_rate = max(persona.rate,
  max(role.rate for role in cast))``; on hit it inserts a themed
  interjection at the slot the next template step would have taken
  (the template step gets bumped to ``seq+1, current=0`` so the kid
  hits it on the next advance — template pointer is NOT advanced).

Tests below exercise both surfaces end-to-end through the production
``POST /propose`` → ``POST /approve`` → ``POST /advance`` chain
(code-quality.md §4: new components need an integration test through
the production caller).

Per-test isolation mirrors :mod:`test_embedded_endings_surfaces`:

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

import hashlib
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


# ---------------------------------------------------------------------
# Surface S — spontaneity advance hook
# ---------------------------------------------------------------------


def _seed_role_assignments_with_trickster(db_path: Path, activity_id: str) -> None:
    """Mutate a persisted activity's summary envelope to include a
    Trickster role assignment so the spontaneity hook's max-rate over
    cast roles picks up the K1 default jokes_rate=0.30 (the highest
    role-side rate in the taxonomy).
    """
    conn = connect(db_path)
    try:
        with conn:
            row = conn.execute(
                "SELECT summary FROM activities WHERE id = ?",
                (activity_id,),
            ).fetchone()
            assert row is not None
            payload = json.loads(row["summary"])
            metadata = dict(payload.get("metadata") or {})
            metadata["role_assignments"] = [
                {
                    "role_name": "trickster",
                    "toy_id": "toy_x_trickster",
                    "generic_descriptor": None,
                    "display_name": "Sneaky Squirrel",
                }
            ]
            metadata["cast_summary"] = "Trickster: Sneaky Squirrel"
            payload["metadata"] = metadata
            conn.execute(
                "UPDATE activities SET summary = ? WHERE id = ?",
                (json.dumps(payload, sort_keys=True), activity_id),
            )
    finally:
        conn.close()


def _seed_role_assignments_with_persona_drives(db_path: Path, activity_id: str) -> None:
    """Cast contains only a low-rate role (Guide/Mentor; 0.05/0.05) so
    the persona-side rate (0.95) dominates the max-rate computation.
    """
    conn = connect(db_path)
    try:
        with conn:
            row = conn.execute(
                "SELECT summary FROM activities WHERE id = ?",
                (activity_id,),
            ).fetchone()
            assert row is not None
            payload = json.loads(row["summary"])
            metadata = dict(payload.get("metadata") or {})
            metadata["role_assignments"] = [
                {
                    "role_name": "guide_mentor",
                    "toy_id": "toy_y_mentor",
                    "generic_descriptor": None,
                    "display_name": "Wise Owl",
                }
            ]
            metadata["cast_summary"] = "Guide / Mentor: Wise Owl"
            payload["metadata"] = metadata
            conn.execute(
                "UPDATE activities SET summary = ? WHERE id = ?",
                (json.dumps(payload, sort_keys=True), activity_id),
            )
    finally:
        conn.close()


def _pin_persona_spontaneity_rates(db_path: Path, persona_id: str, rates: dict[str, float]) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE personas SET spontaneity_rates = ? WHERE id = ?",
                (json.dumps(rates, sort_keys=True), persona_id),
            )
    finally:
        conn.close()


def _force_activity_id(db_path: Path, activity_id: str, new_id: str) -> str:
    """Rewrite an activity row's id (+ its activity_steps + labeled_events
    references) so the spontaneity hash seed lands at a predictable
    roll value for the test. Used only by the deterministic-fire tests
    where we precompute the right id-suffix for the desired roll bucket.
    """
    conn = connect(db_path)
    try:
        with conn:
            # FK chain: activity_steps + labeled_events both reference
            # activities.id. Drop those, rewrite activities.id, then
            # re-insert the children (small per-row counts in tests).
            steps = conn.execute(
                "SELECT * FROM activity_steps WHERE activity_id = ?",
                (activity_id,),
            ).fetchall()
            events = conn.execute(
                "SELECT * FROM labeled_events WHERE activity_id = ?",
                (activity_id,),
            ).fetchall()
            conn.execute(
                "DELETE FROM labeled_events WHERE activity_id = ?",
                (activity_id,),
            )
            conn.execute(
                "DELETE FROM activity_steps WHERE activity_id = ?",
                (activity_id,),
            )
            conn.execute(
                "UPDATE activities SET id = ? WHERE id = ?",
                (new_id, activity_id),
            )
            for s in steps:
                cols = s.keys()
                params = tuple(new_id if c == "activity_id" else s[c] for c in cols)
                placeholders = ",".join("?" for _ in cols)
                conn.execute(
                    f"INSERT INTO activity_steps ({','.join(cols)}) VALUES ({placeholders})",
                    params,
                )
            for e in events:
                cols = e.keys()
                params = tuple(new_id if c == "activity_id" else e[c] for c in cols)
                placeholders = ",".join("?" for _ in cols)
                conn.execute(
                    f"INSERT INTO labeled_events ({','.join(cols)}) VALUES ({placeholders})",
                    params,
                )
    finally:
        conn.close()
    return new_id


def _spontaneity_roll(activity_id: str, new_seq: int) -> float:
    """Reproduce the spontaneity hook's seed → [0,1) roll math so
    tests can precompute whether a given (id, seq) will fire."""
    seed_input = f"{activity_id}:{new_seq}:spontaneity".encode()
    digest = hashlib.sha256(seed_input).digest()
    seed_int = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return seed_int / float(1 << 64)


def _find_low_roll_id(prefix: str, new_seq: int, ceiling: float = 0.05) -> str:
    """Brute-force search for an activity id (deterministic UUIDv4-ish)
    whose spontaneity roll at ``new_seq`` is below ``ceiling`` — so a
    spontaneity_rates configured for that ceiling will reliably fire.
    """
    for i in range(10_000):
        candidate = f"{prefix}-{i:08d}-0000-4000-8000-{i:012x}"
        if _spontaneity_roll(candidate, new_seq) < ceiling:
            return candidate
    raise RuntimeError("no low-roll activity id found in 10k tries")


def _find_high_roll_id(prefix: str, new_seq: int, floor: float = 0.95) -> str:
    for i in range(10_000):
        candidate = f"{prefix}-{i:08d}-0000-4000-8000-{i:012x}"
        if _spontaneity_roll(candidate, new_seq) > floor:
            return candidate
    raise RuntimeError("no high-roll activity id found in 10k tries")


def test_spontaneity_fires_when_role_drives_rate(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k15_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``play_spontaneity_enabled=true`` + a Trickster cast (role
    jokes_rate=0.30) + a pinned activity id whose roll falls under
    0.30, the next advance inserts a spontaneity joke at the upcoming
    seq. Attribution.speaker_kind == "role".
    """
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()
    _set_flag(db_path, "play_spontaneity_enabled", True)

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    # State: proposed, version=1, one step row at seq=1 current.
    # Approve + first advance lands us at running with seq=1 current.
    _walk_to_running_at_step_1(
        client,
        parent_headers,
        activity_id,
        int(activity["version"]),
    )

    # Pin a deterministic id with a low spontaneity roll at new_seq=2,
    # then attach a Trickster cast so the role-side jokes_rate=0.30
    # wins the max.
    target_id = _find_low_roll_id("dead0000", 2, ceiling=0.20)
    _force_activity_id(db_path, activity_id, target_id)
    _seed_role_assignments_with_trickster(db_path, target_id)
    activity_id = target_id

    # Refetch current version after the id rewrite.
    conn = connect(db_path)
    try:
        version_row = conn.execute(
            "SELECT version FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    pre_version = int(version_row["version"])

    advance_resp = client.post(
        f"/api/activities/{activity_id}/advance",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(pre_version)},
    )
    assert advance_resp.status_code == 200, advance_resp.text
    body = cast("dict[str, Any]", advance_resp.json())
    assert body["interjection_pending"] is True, (
        "spontaneity fired → interjection_pending must be True on this advance"
    )

    rows = _fetch_steps(db_path, activity_id)
    # When spontaneity fires: spontaneity step at seq=2 (current=1) +
    # template step at seq=3 (current=0). Original seq=1 stays.
    seqs = sorted(r["seq"] for r in rows)
    assert seqs == [1, 2, 3], (
        f"spontaneity hit must persist both spontaneity + template step; got {seqs}"
    )
    spont_row = next(r for r in rows if r["seq"] == 2)
    template_followup = next(r for r in rows if r["seq"] == 3)
    assert spont_row["current"] is True
    assert spont_row["kind"] == "joke", (
        f"expected joke (role jokes_rate=0.30 wins); got {spont_row['kind']!r}"
    )
    meta = json.loads(spont_row["metadata_json"])
    assert meta["interjection"] == InterjectionKind.spontaneity.value
    attribution = meta["spontaneity_attribution"]
    assert attribution["speaker_kind"] == "role"
    assert attribution["display_name"] == "Sneaky Squirrel"
    assert attribution["role_name"] == "trickster"
    # Template follow-up stays current=0 (template-pointer rule).
    assert template_followup["current"] is False
    assert template_followup["body"] == "Second step.", (
        "spontaneity must not skip the template step that would've come next"
    )


def test_spontaneity_attribution_persona_when_persona_drives(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k15_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the persona's rate > every cast role's rate, attribution
    speaker_kind == "persona".
    """
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()
    _set_flag(db_path, "play_spontaneity_enabled", True)
    _seed_role_weighted_persona(db_path)

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    _walk_to_running_at_step_1(
        client,
        parent_headers,
        activity_id,
        int(activity["version"]),
    )

    # Activity row carries the auto-picked random library persona —
    # since the loader writes the four library personas at startup,
    # we override the activity's persona pointer to k15_persona AND
    # pin that persona's rates above any role rate.
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE activities SET persona_id = 'k15_persona' WHERE id = ?",
                (activity_id,),
            )
    finally:
        conn.close()
    _pin_persona_spontaneity_rates(db_path, "k15_persona", {"jokes": 0.99, "songs": 0.0})
    # Use a cast role with low jokes_rate so the persona's 0.99 wins
    # the max. Guide/Mentor.jokes_rate = 0.05 in K1 defaults.
    _seed_role_assignments_with_persona_drives(db_path, activity_id)

    # Roll < 0.99 (extremely likely); we still pin via id-search to
    # remove flake. Persona-driven jokes_rate=0.99 means almost any
    # roll fires.
    target_id = _find_low_roll_id("face0000", 2, ceiling=0.50)
    _force_activity_id(db_path, activity_id, target_id)
    # Re-seed role records on the renamed row.
    _seed_role_assignments_with_persona_drives(db_path, target_id)
    activity_id = target_id

    conn = connect(db_path)
    try:
        version_row = conn.execute(
            "SELECT version FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    pre_version = int(version_row["version"])

    advance_resp = client.post(
        f"/api/activities/{activity_id}/advance",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(pre_version)},
    )
    assert advance_resp.status_code == 200, advance_resp.text
    body = cast("dict[str, Any]", advance_resp.json())
    assert body["interjection_pending"] is True

    rows = _fetch_steps(db_path, activity_id)
    spont_row = next(r for r in rows if r["seq"] == 2)
    assert spont_row["kind"] == "joke", (
        f"persona jokes_rate=0.99 — expected joke; got {spont_row['kind']!r}"
    )
    meta = json.loads(spont_row["metadata_json"])
    attribution = meta["spontaneity_attribution"]
    assert attribution["speaker_kind"] == "persona", (
        f"persona drove the rate; attribution must be persona, got {attribution['speaker_kind']!r}"
    )
    assert "role_name" not in attribution


def test_spontaneity_disabled_no_interjection(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k15_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``play_spontaneity_enabled = false`` (the K2 default): no
    interjection fires regardless of role/persona rates.
    """
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()
    # Default is false; set explicitly for clarity.
    _set_flag(db_path, "play_spontaneity_enabled", False)

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    _walk_to_running_at_step_1(
        client,
        parent_headers,
        activity_id,
        int(activity["version"]),
    )

    # Pin an id with very low roll AND attach a Trickster so a hook
    # that ignored the surface gate would definitely fire.
    target_id = _find_low_roll_id("beef0000", 2, ceiling=0.20)
    _force_activity_id(db_path, activity_id, target_id)
    _seed_role_assignments_with_trickster(db_path, target_id)
    activity_id = target_id

    conn = connect(db_path)
    try:
        version_row = conn.execute(
            "SELECT version FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    pre_version = int(version_row["version"])

    advance_resp = client.post(
        f"/api/activities/{activity_id}/advance",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(pre_version)},
    )
    assert advance_resp.status_code == 200, advance_resp.text
    body = cast("dict[str, Any]", advance_resp.json())
    assert body.get("interjection_pending") in (False, None)

    rows = _fetch_steps(db_path, activity_id)
    seqs = sorted(r["seq"] for r in rows)
    # No spontaneity fired → just the template step advance: 1 → 2.
    assert seqs == [1, 2], f"spontaneity off — expected normal advance (seqs 1,2); got {seqs}"
    step_two = next(r for r in rows if r["seq"] == 2)
    assert step_two["kind"] is None, (
        f"step 2 must be a plain template step (no kind), got {step_two['kind']!r}"
    )


def test_spontaneity_high_roll_no_fire(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k15_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with spontaneity enabled + a Trickster cast, a high roll
    (>= effective_jokes + effective_songs) does NOT fire. Pins the
    threshold semantics.
    """
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()
    _set_flag(db_path, "play_spontaneity_enabled", True)

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    _walk_to_running_at_step_1(
        client,
        parent_headers,
        activity_id,
        int(activity["version"]),
    )

    # Trickster total max = 0.30 jokes + 0.10 songs = 0.40 ceiling.
    # Persona (auto-picked library) brings its own rates: princess
    # carries jokes=0.05 songs=0.15. Trickster still dominates on
    # jokes (0.30) and matches songs on the library defaults. Pick
    # an id with roll > 0.99 to clear the ceiling under any persona.
    target_id = _find_high_roll_id("babe0000", 2, floor=0.99)
    _force_activity_id(db_path, activity_id, target_id)
    _seed_role_assignments_with_trickster(db_path, target_id)
    activity_id = target_id

    conn = connect(db_path)
    try:
        version_row = conn.execute(
            "SELECT version FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    pre_version = int(version_row["version"])

    advance_resp = client.post(
        f"/api/activities/{activity_id}/advance",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(pre_version)},
    )
    assert advance_resp.status_code == 200, advance_resp.text
    body = cast("dict[str, Any]", advance_resp.json())
    assert body.get("interjection_pending") in (False, None)

    rows = _fetch_steps(db_path, activity_id)
    seqs = sorted(r["seq"] for r in rows)
    assert seqs == [1, 2]
    step_two = next(r for r in rows if r["seq"] == 2)
    assert step_two["kind"] is None


def test_interjection_pending_clears_on_next_advance(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k15_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a spontaneity step fires, the NEXT advance moves the kid
    onto the template-follow-up step (seq=3) and ``interjection_pending``
    flips back to False on the response.
    """
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()
    _set_flag(db_path, "play_spontaneity_enabled", True)

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    _walk_to_running_at_step_1(
        client,
        parent_headers,
        activity_id,
        int(activity["version"]),
    )

    target_id = _find_low_roll_id("cafe0000", 2, ceiling=0.20)
    _force_activity_id(db_path, activity_id, target_id)
    _seed_role_assignments_with_trickster(db_path, target_id)
    activity_id = target_id

    conn = connect(db_path)
    try:
        version_row = conn.execute(
            "SELECT version FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    pre_version = int(version_row["version"])

    first_advance = client.post(
        f"/api/activities/{activity_id}/advance",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(pre_version)},
    )
    assert first_advance.status_code == 200, first_advance.text
    body = first_advance.json()
    assert body["interjection_pending"] is True
    next_version = int(body["version"])

    # Next advance — kid moves off the spontaneity step onto the
    # template step at seq=3. interjection_pending must flip to False.
    second = client.post(
        f"/api/activities/{activity_id}/advance",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(next_version)},
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body.get("interjection_pending") in (False, None), (
        "kid stepped off the spontaneity row; pending must clear"
    )
    rows = _fetch_steps(db_path, activity_id)
    current = next(r for r in rows if r["current"])
    assert current["seq"] == 3
    assert current["kind"] is None
    assert current["body"] == "Second step."


def test_spontaneity_logs_labeled_event_with_attribution(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k15_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spontaneity fires append an interjection event with
    ``source = "spontaneity"`` + attribution dict to ``labeled_events.tool_calls``.
    """
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()
    _set_flag(db_path, "play_spontaneity_enabled", True)

    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    _walk_to_running_at_step_1(
        client,
        parent_headers,
        activity_id,
        int(activity["version"]),
    )

    target_id = _find_low_roll_id("dec0d000", 2, ceiling=0.20)
    _force_activity_id(db_path, activity_id, target_id)
    _seed_role_assignments_with_trickster(db_path, target_id)
    activity_id = target_id

    conn = connect(db_path)
    try:
        version_row = conn.execute(
            "SELECT version FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    pre_version = int(version_row["version"])

    advance_resp = client.post(
        f"/api/activities/{activity_id}/advance",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(pre_version)},
    )
    assert advance_resp.status_code == 200, advance_resp.text

    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT tool_calls FROM labeled_events WHERE activity_id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row["tool_calls"] is not None
    entries = json.loads(row["tool_calls"])
    spontaneity_entries = [
        e for e in entries if e.get("event") == "interjection" and e.get("source") == "spontaneity"
    ]
    assert len(spontaneity_entries) == 1, (
        f"expected exactly one spontaneity event; got {spontaneity_entries}"
    )
    entry = spontaneity_entries[0]
    assert entry["interjection_kind"] == InterjectionKind.spontaneity.value
    assert entry["corpus_entry_id"] == "k15-stub-joke"
    attribution = entry["attribution"]
    assert attribution["speaker_kind"] == "role"
    assert attribution["role_name"] == "trickster"
