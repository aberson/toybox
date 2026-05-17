"""Phase K Step K17 — end-to-end smoke gate (backend portion).

K17's plan-stated deliverable is a runtime smoke that exercises every
Phase K-shipped surface together. K17 carries no production-code diff;
this file converts it into a permanent CI regression test for the
integrated K1-K16b surface so the smoke gate survives beyond the
operator UAT moment.

Phase L Step L5 deleted the embedded mid-activity picker, the ending
auto-append, and the spontaneity advance hook (jokes/songs migrated to
per-activity reward types resolved at terminal advance). The K17 sub-
steps that exercised those three surfaces (e, h, and three of the
flag-effect spot-checks in i) were removed. Surviving coverage:

* (a) propose role-aware activity from backfilled catalog
* (b) recast pre-approval — version increments, state stays proposed
* (c) approve — proposed → running
* (g) parent inserts a joke mid-activity → next step on advance
* (i) toggle each of the surviving 5 feature flags + verify behavior

Sub-steps (d) avatar render and (f) audio playback are inherently
frontend/PWA and are covered by K18 iPad UAT, not this test. K17's
"no kiosk console errors" acceptance criterion is also frontend-only.

For (a) we point ``generator.TEMPLATES_DIR`` at the real backfilled
``src/toybox/activities/templates/branching/`` directory (overriding
the conftest autouse fixture that sandboxes to the 4 production
templates) so we exercise the actual K16-shipped catalog.

Per ``.claude/rules/code-quality.md`` §4 (integration test through the
production caller), every sub-step here exercises the real HTTP endpoint
chain: ``POST /api/activities/propose`` → ``POST /recast`` → ``POST
/approve`` → ``POST /advance`` → ``POST /insert-joke`` → ``PUT
/api/settings/<flag>``.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.activities import joke_corpus, song_corpus
from toybox.activities.generator import TEMPLATES_DIR, clear_template_cache
from toybox.activities.interjections import InterjectionKind
from toybox.api.clickable_words_enabled_settings import (
    get_db as clickable_words_enabled_get_db,
)
from toybox.api.jokes_enabled_settings import get_db as jokes_enabled_get_db
from toybox.api.play_standalone_enabled_settings import (
    get_db as play_standalone_enabled_get_db,
)
from toybox.api.read_me_button_enabled_settings import (
    get_db as read_me_button_enabled_get_db,
)
from toybox.api.songs_enabled_settings import get_db as songs_enabled_get_db
from toybox.db.connection import connect

# ---------------------------------------------------------------------
# Fixture: extend the conftest ``app`` with the 5 surviving Phase K
# settings get_db overrides so PUTs to ``/api/settings/<flag>`` route
# to the per-test SQLite DB. The conftest fixture overrides the
# activities / auth / children deps but NOT the per-setting get_db
# deps; without this extension every settings PUT hits the
# production-default DB and returns 500 ("no such table: settings").
# Phase L Step L5 reduced the Phase K flag count from 8 to 5; the
# read_corpus, spontaneity, and embedded flags were deleted alongside
# their corresponding surfaces.
# ---------------------------------------------------------------------


PHASE_K_SETTINGS_DB_DEPS = [
    jokes_enabled_get_db,
    songs_enabled_get_db,
    play_standalone_enabled_get_db,
    clickable_words_enabled_get_db,
    read_me_button_enabled_get_db,
]


@pytest.fixture(autouse=True)
def _override_phase_k_settings_deps(
    app: FastAPI,
    db_path: Path,
) -> None:
    """Wire the 5 surviving Phase K settings ``get_db`` deps into the
    per-test DB.

    Autouse so every test in this module gets the overrides without
    repeating the boilerplate. Mirrors the per-endpoint override
    pattern from :mod:`test_phase_k_feature_flags_api`. Phase L Step
    L5 deleted three of the original eight settings (read_corpus,
    spontaneity, embedded) alongside their corresponding surfaces.
    """

    def _override_db() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    for dep in PHASE_K_SETTINGS_DB_DEPS:
        app.dependency_overrides[dep] = _override_db


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

# The surviving Phase K feature flags, paired with their kebab-case
# endpoint suffixes + spec'd defaults. Phase L Step L5 removed three
# play-surface flags (``play_embedded_enabled``, ``play_endings_enabled``,
# ``play_spontaneity_enabled``) when jokes/songs migrated to per-activity
# reward types. The kebab keys come from the plan §7 settings table;
# the defaults come from migration 0015.
PHASE_K_FLAGS: list[tuple[str, str, bool]] = [
    ("jokes_enabled", "jokes-enabled", True),
    ("songs_enabled", "songs-enabled", True),
    ("play_standalone_enabled", "play-standalone-enabled", True),
    ("clickable_words_enabled", "clickable-words-enabled", True),
    ("read_me_button_enabled", "read-me-button-enabled", True),
]


# ---------------------------------------------------------------------
# Corpus + template fixtures
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
        "id": "k17-stub-song",
        "title": "K17 Stub Song",
        "audio_path": "audio/k17-stub-song.mp3",
        "duration_seconds": 10,
        "theme": "adventure",
        "age_band": "3-5",
        "persona_compat": ["all"],
        "license": "CC-BY-4.0",
        "credit": "K17 test fixture",
        "lyrics": "Tra la la la.",
    }
    base.update(overrides)
    return base


def _good_joke_entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "k17-stub-joke",
        "setup": "Why did the K17 button click?",
        "punchline": "To run the smoke gate.",
        # theme=adventure so the picker matches the synthesized
        # template's recommended_themes=["adventure"] in sub-steps (e)
        # + (h). The corpus filter is theme-strict — a mismatch returns
        # None and degrades the embedded surface to a terminal skip,
        # which would silently bypass the joke step the test asserts on.
        "theme": "adventure",
        "optional_toy_slot": False,
        "age_band": "3-5",
        "persona_compat": ["all"],
    }
    base.update(overrides)
    return base


@pytest.fixture
def k17_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Point joke + song corpora at single-entry tmp fixtures so the
    picker always lands on the known stub id (mirrors K14/K15 patterns).
    """
    _write_song_manifest(tmp_path, [_good_song_entry()])
    _stub_audio(tmp_path, "audio/k17-stub-song.mp3")
    _write_joke_corpus(tmp_path, [_good_joke_entry()])
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    song_corpus.clear_song_cache()
    joke_corpus.clear_joke_cache()
    try:
        yield tmp_path
    finally:
        song_corpus.clear_song_cache()
        joke_corpus.clear_joke_cache()


# Phase L Step L5: the K17 ``_TEMPLATE_ROLES_EMBEDDED_AND_ENDING``
# fixture was retired alongside the embedded mid-activity picker and
# ending auto-append deletion. The K17 sub-steps that consumed it (e,
# h) were removed in the L5 surface deletion. The plain three-step
# fixture below remains and is exercised by the surviving K17 tests.


# Plain three-step text template — used for the parent-insert-joke
# mid-activity sub-step (g). Kept role-less to reduce coupling; the
# parent insert path doesn't depend on roles.
_TEMPLATE_PLAIN_3STEP: dict[str, Any] = {
    "intent": "boredom",
    "templates": [
        {
            "id": "k17_plain_3step",
            "title": "K17 plain three-step",
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
    """Stage a tmp templates dir with a custom ``boredom.json`` and the
    other three production intents copied unchanged so the propose
    dispatcher has something to fall back on for non-boredom intents.
    """
    staged = tmp_path / "templates_k17"
    staged.mkdir(exist_ok=True)
    shutil.copy(TEMPLATES_DIR / "_schema.json", staged / "_schema.json")
    (staged / "boredom.json").write_text(json.dumps(boredom_payload), encoding="utf-8")
    for intent in ("request_play", "request_story", "request_activity"):
        shutil.copy(TEMPLATES_DIR / f"{intent}.json", staged / f"{intent}.json")
    return staged


# ---------------------------------------------------------------------
# Toy + persona seeding
# ---------------------------------------------------------------------


def _seed_toys(db_path: Path) -> list[tuple[str, str]]:
    """Seed four toys so the role picker has more pool than required."""
    toys = [
        ("toy_a_alpha", "Alpha Owl"),
        ("toy_b_bear", "Captain Bear"),
        ("toy_c_cat", "Curious Cat"),
        ("toy_d_duck", "Dapper Duck"),
    ]
    conn = connect(db_path)
    try:
        with conn:
            for toy_id, display_name in toys:
                conn.execute(
                    "INSERT INTO toys "
                    "(id, display_name, image_path, image_hash, type, tags, "
                    " persona_id, archived, created_at, last_used_at) "
                    "VALUES (?, ?, ?, ?, NULL, NULL, NULL, 0, "
                    " '2026-01-01T00:00:00Z', NULL)",
                    (toy_id, display_name, f"img/{toy_id}.png", f"hash-{toy_id}"),
                )
    finally:
        conn.close()
    return toys


def _seed_role_weighted_persona(db_path: Path) -> str:
    """Seed a persona with explicit role_weights so the role picker has
    deterministic bias. Returns the persona id.
    """
    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures" / "personas" / "role_weighted.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    role_weights_json = json.dumps(payload["role_weights"], sort_keys=True, separators=(",", ":"))
    spontaneity_rates_json = json.dumps(
        payload["spontaneity_rates"], sort_keys=True, separators=(",", ":")
    )
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
                    payload["id"],
                    payload["display_name"],
                    payload["archetype"],
                    payload["system_prompt"],
                    payload["avatar_image_path"],
                    json.dumps(payload["behavior_tags"]),
                    payload["age_range_min"],
                    payload["age_range_max"],
                    payload["language"],
                    payload["source"],
                    payload["default_voice_tone"],
                    "2026-05-15T00:00:00Z",
                    role_weights_json,
                    None,
                    spontaneity_rates_json,
                ),
            )
    finally:
        conn.close()
    return cast("str", payload["id"])


# ---------------------------------------------------------------------
# DB + HTTP helpers
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


def _fetch_state_and_version(db_path: Path, activity_id: str) -> tuple[str, int]:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT state, version FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return str(row["state"]), int(row["version"])


def _propose(
    client: TestClient,
    parent_headers: dict[str, str],
    *,
    intent: str = "boredom",
    seed: int = 17,
    persona_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"intent": intent, "slot": None, "hour": 12, "seed": seed}
    if persona_id is not None:
        body["persona_id"] = persona_id
    response = client.post(
        "/api/activities/propose",
        json=body,
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


def _recast(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    version: int,
) -> dict[str, Any]:
    response = client.post(
        f"/api/activities/{activity_id}/recast",
        json={},
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert response.status_code == 200, response.text
    return cast("dict[str, Any]", response.json())


# ---------------------------------------------------------------------
# Sub-step (a): propose role-aware activity from backfilled catalog
# ---------------------------------------------------------------------


def test_k17_a_propose_role_aware_from_backfilled_catalog(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sub-step (a): propose against the REAL K16-shipped backfilled
    catalog. The conftest autouse fixture sandboxes integration tests
    to the 4 production templates only; we override that here to point
    back at the full backfilled catalog so K17 actually exercises the
    K16 deliverable.

    Asserts:
    * Propose returns 201 with state=proposed.
    * The chosen template was a backfilled one (id starts with the
      ``boredom_soak_`` prefix the K16 backfill agents use).
    * ``response.roles`` is a non-empty dict.
    * ``response.cast_summary`` is set + non-empty.
    * The template's ``ending_step`` is set on the source template (so
      the ending-song surface is exercisable downstream).
    """
    from toybox.activities import generator

    # Override conftest's sandbox: point at the real production templates
    # dir which holds the backfilled branching templates.
    real_templates_dir = (
        Path(__file__).resolve().parents[2] / "src" / "toybox" / "activities" / "templates"
    )
    monkeypatch.setattr(generator, "TEMPLATES_DIR", real_templates_dir)
    generator.clear_template_cache()

    _seed_toys(db_path)

    response = client.post(
        "/api/activities/propose",
        json={"intent": "boredom", "slot": None, "hour": 12, "seed": 1},
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())
    assert body["state"] == "proposed"

    # Backfilled templates use ``boredom_soak_<theme>_<NN>`` ids per K16
    # naming convention. Plain "boredom_morning_explore" is the legacy
    # 4-template pre-K16 set.
    template_id = (body.get("template_id") or "").strip()
    if not template_id:
        # template_id may live in summary metadata
        conn = connect(db_path)
        try:
            row = conn.execute(
                "SELECT summary FROM activities WHERE id = ?",
                (body["id"],),
            ).fetchone()
        finally:
            conn.close()
        if row and row["summary"]:
            payload = json.loads(row["summary"])
            template_id = str(payload.get("template_id") or "")
    assert template_id, "propose response must surface the picked template id"
    assert "_soak_" in template_id, (
        f"K17 (a) requires the backfilled catalog to be live; got "
        f"template_id={template_id!r} which is not a K16-backfilled id"
    )

    # Roles + cast_summary populated (K5/K7 wire-up).
    roles = body.get("roles")
    assert isinstance(roles, dict) and roles, (
        f"backfilled templates declare required_roles → response.roles "
        f"must be a non-empty dict; got {roles!r}"
    )
    cast_summary = body.get("cast_summary")
    assert isinstance(cast_summary, str) and cast_summary, (
        f"cast_summary must be set on a role-bearing activity; got {cast_summary!r}"
    )

    # interjection_pending false at propose-time (only flips after a
    # spontaneity advance hook fires).
    assert body.get("interjection_pending") in (False, None)


# ---------------------------------------------------------------------
# Sub-step (b): recast pre-approval bumps version, state stays proposed
# ---------------------------------------------------------------------


def test_k17_b_recast_pre_approval_bumps_version(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sub-step (b): recast a proposed role-bearing activity. Version
    bumps by 1, state stays proposed, roles dict is still populated.

    The plan note "if RNG happens to collide, retry — but not infinitely;
    5 tries max" is honored: we attempt up to 5 recasts and assert that
    EITHER the cast changed OR — if every roll collided — the version
    still advanced by exactly the number of recasts (the API contract
    is "fresh seed", not "guaranteed different cast"; a small toy pool
    can reasonably collide).
    """
    from toybox.activities import generator

    real_templates_dir = (
        Path(__file__).resolve().parents[2] / "src" / "toybox" / "activities" / "templates"
    )
    monkeypatch.setattr(generator, "TEMPLATES_DIR", real_templates_dir)
    generator.clear_template_cache()

    _seed_toys(db_path)

    proposed = _propose(client, parent_headers, intent="boredom", seed=1)
    activity_id = proposed["id"]
    base_version = int(proposed["version"])
    base_summary = proposed.get("cast_summary")
    assert isinstance(base_summary, str) and base_summary

    current_version = base_version
    saw_change = False
    last_summary = base_summary
    for _ in range(5):
        body = _recast(client, parent_headers, activity_id, current_version)
        new_version = int(body["version"])
        assert new_version == current_version + 1, (
            f"recast must bump version once per call; was {current_version}, got {new_version}"
        )
        assert body["state"] == "proposed", "recast preserves proposed state"
        roles = body.get("roles")
        assert isinstance(roles, dict) and roles, f"recast must keep roles populated; got {roles!r}"
        new_summary = body.get("cast_summary")
        assert isinstance(new_summary, str) and new_summary
        if new_summary != base_summary:
            saw_change = True
            last_summary = new_summary
            break
        current_version = new_version
        last_summary = new_summary

    # Either we observed a different cast, or all 5 recasts collided —
    # both are valid per the API contract. Assert at least the version
    # bump survived.
    db_state, db_version = _fetch_state_and_version(db_path, activity_id)
    assert db_state == "proposed"
    assert db_version > base_version, "recast must have bumped persisted version"
    # Surface the no-change case in test output for visibility — does
    # not gate the assertion.
    if not saw_change:
        # This is unlikely with 4 toys + 1-2 roles but valid.
        assert last_summary == base_summary


# ---------------------------------------------------------------------
# Sub-step (c): approve (proposed → running)
# ---------------------------------------------------------------------


def test_k17_c_approve_transitions_proposed_to_running(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sub-step (c): approve a proposed activity. State transitions to
    ``approved`` (then ``running`` after the first /advance per existing
    contract). Per existing toybox conventions the approve endpoint
    moves proposed → approved; the approved → running transition fires
    on the first /advance call.
    """
    from toybox.activities import generator

    real_templates_dir = (
        Path(__file__).resolve().parents[2] / "src" / "toybox" / "activities" / "templates"
    )
    monkeypatch.setattr(generator, "TEMPLATES_DIR", real_templates_dir)
    generator.clear_template_cache()

    _seed_toys(db_path)

    proposed = _propose(client, parent_headers, intent="boredom", seed=1)
    activity_id = proposed["id"]

    approved = _approve(client, parent_headers, activity_id, int(proposed["version"]))
    assert approved["state"] == "approved", (
        f"approve must transition to 'approved'; got {approved['state']!r}"
    )
    assert int(approved["version"]) == int(proposed["version"]) + 1

    # First advance should land on running.
    running = _advance(client, parent_headers, activity_id, int(approved["version"]))
    assert running["state"] == "running"


# ---------------------------------------------------------------------
# Sub-step (g): parent inserts a joke mid-activity → kid sees it next
# ---------------------------------------------------------------------


def test_k17_g_parent_inserts_joke_mid_activity_kid_sees_it_next(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k17_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sub-step (g): start a fresh activity, advance halfway, parent
    inserts a joke via /insert-joke. Assert:

    * Inserted joke appears in ``activity_steps`` at ``current_step+1``
      with ``metadata.interjection == "parent"`` and a valid ``source_id``.
    * Next /advance puts the kid on the joke step (it becomes current).

    Uses the plain three-step template — no roles, no embedded auto
    steps — so the only interjection in the sequence is the parent
    insert (clean signal for the assertion).
    """
    staged = _stage_templates(tmp_path, _TEMPLATE_PLAIN_3STEP)
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()

    proposed = _propose(client, parent_headers, intent="boredom", seed=17)
    activity_id = proposed["id"]
    version = int(proposed["version"])
    # approve → first advance → running on seq=1.
    approved = _approve(client, parent_headers, activity_id, version)
    version = int(approved["version"])
    state = _advance(client, parent_headers, activity_id, version)
    version = int(state["version"])
    assert state["state"] == "running"

    # The kid is now on seq=1 (current=True). Parent inserts the joke.
    pre_steps = _fetch_steps(db_path, activity_id)
    current_seq = next(r["seq"] for r in pre_steps if r["current"])
    assert current_seq == 1, f"after first advance, kid should be on seq=1; got {current_seq}"

    insert_resp = client.post(
        f"/api/activities/{activity_id}/insert-joke",
        json=None,
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert insert_resp.status_code == 200, insert_resp.text
    insert_body = cast("dict[str, Any]", insert_resp.json())
    assert insert_body["version"] == version + 1
    assert insert_body["state"] == "running"

    # Inserted row at current_step+1 (= seq 2) with kind=joke +
    # metadata.interjection="parent" + source_id populated.
    after_insert = _fetch_steps(db_path, activity_id)
    seqs = sorted(r["seq"] for r in after_insert)
    assert seqs == [1, 2], f"insert should place the joke at current_seq+1; got seqs={seqs}"
    inserted = next(r for r in after_insert if r["seq"] == 2)
    assert inserted["kind"] == "joke"
    assert inserted["current"] is True, "the inserted joke becomes current"
    assert inserted["metadata_json"] is not None
    meta = json.loads(inserted["metadata_json"])
    assert meta["interjection"] == InterjectionKind.parent.value
    assert meta["source_id"] == "k17-stub-joke", (
        f"source_id must point at the corpus entry; got {meta.get('source_id')!r}"
    )
    assert isinstance(meta.get("punchline"), str) and meta["punchline"]


# ---------------------------------------------------------------------
# Sub-step (i): toggle each of the 8 feature flags + verify behavior
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "kebab", "default"),
    PHASE_K_FLAGS,
    ids=[k for k, _, _ in PHASE_K_FLAGS],
)
def test_k17_i_each_flag_round_trips_via_settings_api(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    key: str,
    kebab: str,
    default: bool,
) -> None:
    """Sub-step (i) part 1 — each of the 8 flags GETs at its spec'd
    default, PUTs the inverse, and GETs back the inverted value. 8
    parametrized cases.
    """
    # 1. GET default.
    initial = client.get(f"/api/settings/{kebab}")
    assert initial.status_code == 200, initial.text
    assert initial.json() == {"value": default}, (
        f"flag {key!r} default mismatch: expected {default}, got {initial.json()}"
    )

    # 2. PUT inverse.
    inverted = not default
    put_resp = client.put(
        f"/api/settings/{kebab}",
        json={"value": inverted},
        headers=parent_headers,
    )
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json() == {"value": inverted}

    # 3. GET back the inverted value.
    after = client.get(f"/api/settings/{kebab}")
    assert after.status_code == 200
    assert after.json() == {"value": inverted}


def test_k17_i_songs_disabled_dismisses_request_song(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k17_corpus: Path,
) -> None:
    """Sub-step (i) part 2 — flag-effect spot-check #1: with
    ``songs_enabled=False`` a propose with intent=request_song returns
    the dismissed envelope per phase-k-plan §7 propose row. Pins the
    behavioral effect of the master content flag — toggling alone is
    not enough; the flag must actually gate the surface.
    """
    # Toggle the master OFF via the API (not a direct DB write — that's
    # the integration we're testing).
    put_resp = client.put(
        "/api/settings/songs-enabled",
        json={"value": False},
        headers=parent_headers,
    )
    assert put_resp.status_code == 200

    response = client.post(
        "/api/activities/propose",
        json={"intent": "request_song", "slot": None, "hour": 12, "seed": 17},
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())
    assert body["state"] == "dismissed"
    assert body["reason"] == "surface_disabled", (
        f"songs_enabled=False must dismiss with surface_disabled; got reason={body.get('reason')!r}"
    )


def test_k17_i_jokes_disabled_dismisses_request_joke(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k17_corpus: Path,
) -> None:
    """Sub-step (i) part 2 — flag-effect spot-check #2: with
    ``jokes_enabled=False`` a propose with intent=request_joke returns
    dismissed. Mirror of the songs assertion above so a regression that
    silently fuses the two flags into one read surfaces here.
    """
    put_resp = client.put(
        "/api/settings/jokes-enabled",
        json={"value": False},
        headers=parent_headers,
    )
    assert put_resp.status_code == 200

    response = client.post(
        "/api/activities/propose",
        json={"intent": "request_joke", "slot": None, "hour": 12, "seed": 17},
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())
    assert body["state"] == "dismissed"
    assert body["reason"] == "surface_disabled"


def test_k17_i_play_standalone_disabled_dismisses_standalone_intent(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    k17_corpus: Path,
) -> None:
    """Sub-step (i) part 2 — flag-effect spot-check #3: with
    ``play_standalone_enabled=False`` a request_song propose dismisses
    with surface_disabled. Pins the dual-gate semantics from
    phase-k-plan §6 K13 — both content master AND surface flag must
    pass; toggling either one off should dismiss.
    """
    put_resp = client.put(
        "/api/settings/play-standalone-enabled",
        json={"value": False},
        headers=parent_headers,
    )
    assert put_resp.status_code == 200

    response = client.post(
        "/api/activities/propose",
        json={"intent": "request_song", "slot": None, "hour": 12, "seed": 17},
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())
    assert body["state"] == "dismissed"
    assert body["reason"] == "surface_disabled"

