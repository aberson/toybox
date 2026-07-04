"""Phase Z Z4 — spoken-clip wire shape through the PRODUCTION callers.

Per code-quality.md §4 (new components require an integration test
through the production caller): these tests drive the real FastAPI
routes (propose → approve → advance / insert-joke / reward walk) with
the app's TTS worker lifespan running against the STUB engine
(``TOYBOX_TTS_STUB=1``) and assert the full producer → consumer chain:

    approve/insert  →  step ``metadata_json`` carries ``spoken_*_url``
                    →  background worker renders the WAV into
                       ``data/tts/<voice>/<sha16>.wav``
                    →  GET the persisted URL through the app's static
                       mount returns 200 with WAV bytes.

Also pinned here:

* enqueue NEVER happens in the propose path (plan §6) — propose only
  DERIVES preview URLs;
* the approve WS ``activity.state`` payload carries the new keys;
* approve is non-blocking even when synth is wedged (deterministic:
  the synth is parked on a ``threading.Event`` the test controls — a
  blocking enqueue would deadlock the request, not just be slow);
* the persona ``voice_profile.neural_voice`` threads into every URL,
  with ``DEFAULT_NEURAL_VOICE`` as the no-persona fallback;
* song surfaces (parent insert-song) get NO spoken keys and no synth.

Reviewer note (code-quality.md "audit wire shape"): treat any future
diff that RELAXES these envelope assertions as suspect.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.activities import joke_corpus, song_corpus
from toybox.activities.generator import TEMPLATES_DIR, clear_template_cache
from toybox.app import tts_worker_lifespan
from toybox.core.game_linearity import set_game_linearity
from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
from toybox.tts import worker as tts_worker_mod
from toybox.tts.cache import TTS_AUDIO_URL_PREFIX, clip_path, clip_url
from toybox.tts.engine import (
    DEFAULT_NEURAL_VOICE,
    reset_engine_cache_for_tests,
)
from toybox.tts.worker import get_tts_worker, reset_tts_worker_for_tests
from toybox.ws.topics import Topic

# ---------------------------------------------------------------------
# Environment + client fixtures.
#
# ``tts_env`` MUST be listed before ``app`` in dependent fixtures so
# the env vars are set before ``create_app()`` resolves the static
# mount directory (StaticFiles captures the path at mount time).
# ---------------------------------------------------------------------


@pytest.fixture
def tts_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Stub engine + isolated data root + fresh worker/engine singletons."""
    data_root = tmp_path / "z4_data"
    data_root.mkdir()
    monkeypatch.setenv("TOYBOX_TTS_STUB", "1")
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(data_root))
    reset_tts_worker_for_tests()
    reset_engine_cache_for_tests()
    try:
        yield data_root
    finally:
        reset_tts_worker_for_tests()
        reset_engine_cache_for_tests()


@pytest.fixture
def tts_client(tts_env: Path, app: FastAPI) -> Iterator[TestClient]:
    """TestClient with the production TTS worker lifespan attached.

    Mirrors the ``toybox.main`` lifespan wiring (the worker starts on
    the app's event loop and stops on shutdown) so the enqueue hooks
    exercise the same ``call_soon_threadsafe`` hand-off production
    uses from the sync handler threadpool.
    """

    @contextlib.asynccontextmanager
    async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
        async with tts_worker_lifespan(application):
            yield

    app.router.lifespan_context = _lifespan
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------
# Template + corpus staging
# ---------------------------------------------------------------------

# Choice + question probe template: step 1 linear (explicit next),
# step 2 branches, step 3 carries an R3 question. No slot placeholders
# so rendered texts are byte-stable for hash assertions.
_S1_TEXT = "Step one spoken aloud for the kiosk."
_S2_TEXT = "Pick a path through the castle now!"
_S2_LABELS = ("Go left toward the tower", "Go right toward the moat")
_S3_TEXT = "What does Miss Maple think about all this?"
_S3_QUESTION = "What colour is the sky on a clear day?"

_TEMPLATE_TTS_PROBE: dict[str, Any] = {
    "intent": "boredom",
    "templates": [
        {
            "id": "z4_spoken_probe",
            "title": "A spoken adventure",
            "buckets": ["always"],
            "recommended_themes": ["adventure"],
            "steps": [
                {"id": "s1", "text": _S1_TEXT, "next": "s2"},
                {
                    "id": "s2",
                    "text": _S2_TEXT,
                    "choices": [
                        {"label": _S2_LABELS[0], "next": "s3"},
                        {"label": _S2_LABELS[1], "next": "s3"},
                    ],
                },
                {
                    "id": "s3",
                    "text": _S3_TEXT,
                    "question": _S3_QUESTION,
                    "expected_answer": "blue",
                },
            ],
        }
    ],
}

# Plain 3-step linear template for the reward walk (no question step —
# a Q&A gate would block the terminal advance).
_TEMPLATE_REWARD_WALK: dict[str, Any] = {
    "intent": "boredom",
    "templates": [
        {
            "id": "z4_reward_walk",
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

_JOKE_SETUP = "Why did the Z4 chicken cross the road?"
_JOKE_PUNCHLINE = "To reach the spoken clip on the other side."


def _stage_templates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boredom_payload: dict[str, Any],
) -> None:
    staged = tmp_path / "templates_z4"
    staged.mkdir()
    shutil.copy(TEMPLATES_DIR / "_schema.json", staged / "_schema.json")
    (staged / "boredom.json").write_text(json.dumps(boredom_payload), encoding="utf-8")
    for intent in ("request_play", "request_story", "request_activity"):
        shutil.copy(TEMPLATES_DIR / f"{intent}.json", staged / f"{intent}.json")
    monkeypatch.setattr("toybox.activities.generator.TEMPLATES_DIR", staged)
    clear_template_cache()


def _stage_joke_corpus(data_root: Path) -> None:
    jokes_dir = data_root / "jokes"
    jokes_dir.mkdir(parents=True, exist_ok=True)
    (jokes_dir / "jokes.json").write_text(
        json.dumps(
            [
                {
                    "id": "z4-stub-joke",
                    "setup": _JOKE_SETUP,
                    "punchline": _JOKE_PUNCHLINE,
                    "theme": "adventure",
                    "optional_toy_slot": False,
                    "age_band": "3-5",
                    "persona_compat": ["all"],
                }
            ]
        ),
        encoding="utf-8",
    )
    joke_corpus.clear_joke_cache()


def _stage_song_corpus(data_root: Path) -> None:
    songs_dir = data_root / "songs"
    songs_dir.mkdir(parents=True, exist_ok=True)
    (songs_dir / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "id": "z4-stub-song",
                    "title": "The Z4 Clip Song",
                    "audio_path": "audio/z4-stub-song.mp3",
                    "duration_seconds": 10,
                    "theme": "adventure",
                    "age_band": "3-5",
                    "persona_compat": ["all"],
                    "license": "CC-BY-4.0",
                    "credit": "Z4 test fixture",
                    "lyrics": "La la la.",
                }
            ]
        ),
        encoding="utf-8",
    )
    audio = songs_dir / "audio" / "z4-stub-song.mp3"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"\x00" * 32)
    song_corpus.clear_song_cache()


@pytest.fixture
def _clear_corpora() -> Iterator[None]:
    yield
    joke_corpus.clear_joke_cache()
    song_corpus.clear_song_cache()


# ---------------------------------------------------------------------
# REST helpers (mirror the L4 suite's pattern)
# ---------------------------------------------------------------------


def _propose(
    client: TestClient,
    parent_headers: dict[str, str],
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = body or {"intent": "boredom", "slot": None, "hour": 12, "seed": 17}
    resp = client.post("/api/activities/propose", json=payload, headers=parent_headers)
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


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
    resp = client.post(
        f"/api/activities/{activity_id}/approve",
        json=body,
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert resp.status_code == 200, resp.text
    return cast("dict[str, Any]", resp.json())


def _advance(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    version: int,
    *,
    choice_index: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if choice_index is not None:
        body["choice_index"] = choice_index
    resp = client.post(
        f"/api/activities/{activity_id}/advance",
        json=body,
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert resp.status_code == 200, resp.text
    return cast("dict[str, Any]", resp.json())


def _current_step(state: dict[str, Any]) -> dict[str, Any]:
    for step in state.get("steps") or []:
        if step.get("current"):
            return cast("dict[str, Any]", step)
    raise AssertionError(f"no current step in {state.get('steps')!r}")


def _db_step_metadata(db_path: Path, activity_id: str) -> dict[int, dict[str, Any]]:
    """Persisted ``metadata_json`` per seq — the durable source of truth."""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT seq, metadata_json FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC",
            (activity_id,),
        ).fetchall()
    finally:
        conn.close()
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        raw = r["metadata_json"]
        out[int(r["seq"])] = json.loads(raw) if raw else {}
    return out


def _wait_for_files(paths: list[Path], timeout: float = 10.0) -> None:
    """Bounded poll until every path exists (stub renders are ~instant)."""
    deadline = time.monotonic() + timeout
    remaining = list(paths)
    while remaining and time.monotonic() < deadline:
        remaining = [p for p in remaining if not p.is_file()]
        if remaining:
            time.sleep(0.02)
    assert not remaining, f"worker never rendered: {remaining}"


def _drain_ws(sub: Any) -> list[Any]:
    import asyncio

    envelopes = []
    while True:
        try:
            envelopes.append(sub.get_nowait())
        except asyncio.QueueEmpty:
            return envelopes


_INSERT_PERSONA_SQL = (
    "INSERT INTO personas "
    "(id, display_name, archetype, system_prompt, avatar_image_path, source, "
    " created_at, voice_profile) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


def _seed_wizard_persona(
    db_path: Path,
    *,
    voice_profile: str | None = '{"rate": 0.9, "pitch": 0.7, "neural_voice": "am_michael"}',
) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                _INSERT_PERSONA_SQL,
                (
                    "wizard",
                    "Marvelous the Wizard",
                    "wizard",
                    "system prompt",
                    "avatars/wizard.png",
                    "library",
                    "2026-07-03T00:00:00Z",
                    voice_profile,
                ),
            )
    finally:
        conn.close()


# =====================================================================
# 1. The full round trip: approve → metadata → render → serve
# =====================================================================


def test_approve_round_trip_metadata_render_and_serve(
    tts_env: Path,
    tts_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    pubsub: PubSub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Template activity: approve persists + enqueues, the worker
    renders, and the persisted URL serves 200 WAV through the app."""
    _stage_templates(tmp_path, monkeypatch, _TEMPLATE_TTS_PROBE)
    voice = DEFAULT_NEURAL_VOICE  # no persona seeded → fallback voice

    proposed = _propose(tts_client, parent_headers)
    activity_id = proposed["id"]

    sub = pubsub.subscribe([Topic.activity_state])
    try:
        approved = _approve(tts_client, parent_headers, activity_id, proposed["version"])
        ws_envelopes = _drain_ws(sub)
    finally:
        sub.close()

    # --- approve response (preview path) carries the derived keys ----
    steps = approved["steps"]
    assert len(steps) == 3
    assert steps[0]["metadata"]["spoken_audio_url"] == clip_url(voice, _S1_TEXT)
    s2_meta = steps[1]["metadata"]
    assert s2_meta["spoken_audio_url"] == clip_url(voice, _S2_TEXT)
    # Choice-label shape decision: ``spoken_choice_audio_urls`` is a
    # list aligned index-for-index with ``choices`` (Z5 consumes both
    # by the same index).
    assert s2_meta["spoken_choice_audio_urls"] == [
        clip_url(voice, _S2_LABELS[0]),
        clip_url(voice, _S2_LABELS[1]),
    ]
    labels = [choice["label"] for choice in steps[1]["choices"]]
    assert labels == list(_S2_LABELS)
    s3_meta = steps[2]["metadata"]
    assert s3_meta["spoken_audio_url"] == clip_url(voice, _S3_TEXT)
    assert s3_meta["spoken_question_audio_url"] == clip_url(voice, _S3_QUESTION)

    # --- WS surface: the approve broadcast carries the same keys -----
    approve_payloads = [
        env.payload
        for env in ws_envelopes
        if env.topic is Topic.activity_state
        and env.payload.get("id") == activity_id
        and env.payload.get("state") == "approved"
    ]
    assert approve_payloads, "no approved activity.state envelope observed"
    ws_steps = approve_payloads[-1]["steps"]
    assert ws_steps[0]["metadata"]["spoken_audio_url"] == clip_url(voice, _S1_TEXT)
    assert ws_steps[1]["metadata"]["spoken_choice_audio_urls"] == [
        clip_url(voice, _S2_LABELS[0]),
        clip_url(voice, _S2_LABELS[1]),
    ]
    assert ws_steps[2]["metadata"]["spoken_question_audio_url"] == clip_url(voice, _S3_QUESTION)

    # --- persisted rows (running state = the kiosk's source of truth)
    state = _advance(tts_client, parent_headers, activity_id, approved["version"])
    assert state["state"] == "running"
    persisted = _db_step_metadata(db_path, activity_id)
    assert persisted[1]["spoken_audio_url"] == clip_url(voice, _S1_TEXT)

    # Lazy-inserted step 2 (rule 2 explicit next) carries body + choice
    # clips; lazy-inserted step 3 carries the question clip.
    state = _advance(tts_client, parent_headers, activity_id, state["version"])
    state = _advance(tts_client, parent_headers, activity_id, state["version"], choice_index=0)
    persisted = _db_step_metadata(db_path, activity_id)
    assert persisted[2]["spoken_audio_url"] == clip_url(voice, _S2_TEXT)
    assert persisted[2]["spoken_choice_audio_urls"] == [
        clip_url(voice, _S2_LABELS[0]),
        clip_url(voice, _S2_LABELS[1]),
    ]
    assert persisted[3]["spoken_audio_url"] == clip_url(voice, _S3_TEXT)
    assert persisted[3]["spoken_question_audio_url"] == clip_url(voice, _S3_QUESTION)

    # --- worker rendered every enqueued text (full plan at approve) --
    all_texts = [_S1_TEXT, _S2_TEXT, *_S2_LABELS, _S3_TEXT, _S3_QUESTION]
    _wait_for_files([clip_path(voice, text) for text in all_texts])

    # --- and the persisted URL serves through the production mount ---
    url = cast("str", persisted[1]["spoken_audio_url"])
    assert url.startswith(f"{TTS_AUDIO_URL_PREFIX}/{voice}/")
    resp = tts_client.get(url)
    assert resp.status_code == 200, url
    assert resp.headers["content-type"].startswith("audio/")
    assert resp.content[:4] == b"RIFF"
    assert resp.content[8:12] == b"WAVE"


# =====================================================================
# 2. Propose NEVER enqueues (derived preview URLs only)
# =====================================================================


def test_propose_derives_urls_but_enqueues_nothing(
    tts_env: Path,
    tts_client: TestClient,
    parent_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stage_templates(tmp_path, monkeypatch, _TEMPLATE_TTS_PROBE)
    proposed = _propose(tts_client, parent_headers)

    # The preview steps DO carry derived URLs (pure derivation)...
    assert proposed["steps"][0]["metadata"]["spoken_audio_url"] == clip_url(
        DEFAULT_NEURAL_VOICE, _S1_TEXT
    )

    # ...but nothing was enqueued and nothing renders (plan §6:
    # proposals are speculative; most are dismissed).
    worker = get_tts_worker()
    assert worker is not None
    assert worker.queue_size == 0
    assert worker.rendered_count == 0
    clips_dir = tts_env / "tts"
    assert not list(clips_dir.rglob("*.wav")) if clips_dir.exists() else True


# =====================================================================
# 3. Adventure beats (generated during play)
# =====================================================================


def test_adventure_beat_insert_persists_and_renders(
    tts_env: Path,
    app: FastAPI,
    tts_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    from toybox.api.activities import get_sync_ai_client

    # Offline generation (no Claude), nonlinear so beats carry choices.
    app.dependency_overrides[get_sync_ai_client] = lambda: None
    conn = connect(db_path, check_same_thread=False)
    try:
        set_game_linearity(conn, "nonlinear")
    finally:
        conn.close()

    voice = DEFAULT_NEURAL_VOICE
    proposed = _propose(
        tts_client,
        parent_headers,
        {
            "intent": "request_play",
            "slot": "freeplay",
            "hour": 12,
            "seed": 99,
            "adventure": True,
        },
    )
    activity_id = proposed["id"]

    approved = _approve(tts_client, parent_headers, activity_id, proposed["version"])
    # Beat 0 was persisted at propose → annotated at approve.
    persisted = _db_step_metadata(db_path, activity_id)
    beat0_body = _current_step(approved)["body"]
    assert persisted[1]["spoken_audio_url"] == clip_url(voice, beat0_body)

    # First advance: approved → running (no insert). Second advance
    # resolves the choice and INSERTs beat 2 via _insert_adventure_beat.
    state = _advance(tts_client, parent_headers, activity_id, approved["version"])
    current = _current_step(state)
    ci = 0 if current.get("choices") else None
    state = _advance(tts_client, parent_headers, activity_id, state["version"], choice_index=ci)
    beat2 = _current_step(state)
    assert beat2["kind"] in ("adventure_beat", "boss_fight")

    persisted = _db_step_metadata(db_path, activity_id)
    beat2_meta = persisted[2]
    assert beat2_meta["spoken_audio_url"] == clip_url(voice, beat2["body"])
    if beat2.get("choices"):
        expected = [clip_url(voice, c["label"]) for c in beat2["choices"]]
        assert beat2_meta["spoken_choice_audio_urls"] == expected

    # The wire response mirrors the persisted row (running state reads
    # activity_steps directly).
    assert beat2["metadata"]["spoken_audio_url"] == beat2_meta["spoken_audio_url"]

    # Worker renders the beat body; the persisted URL serves 200 WAV.
    _wait_for_files([clip_path(voice, beat2["body"])])
    resp = tts_client.get(cast("str", beat2_meta["spoken_audio_url"]))
    assert resp.status_code == 200
    assert resp.content[:4] == b"RIFF"


# =====================================================================
# 4. Reward resolve (joke rewards get the setup/punchline pair)
# =====================================================================


def test_reward_joke_step_carries_setup_punchline_clip_urls(
    tts_env: Path,
    tts_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _clear_corpora: None,
) -> None:
    _stage_templates(tmp_path, monkeypatch, _TEMPLATE_REWARD_WALK)
    _stage_joke_corpus(tts_env)
    voice = DEFAULT_NEURAL_VOICE

    proposed = _propose(tts_client, parent_headers)
    activity_id = proposed["id"]
    state = _approve(
        tts_client, parent_headers, activity_id, proposed["version"], reward_type="joke"
    )
    # Walk the 3 steps; the 4th advance fires the reward resolve
    # (Phase-1 of the two-phase terminal advance — state stays running).
    for _ in range(4):
        state = _advance(tts_client, parent_headers, activity_id, int(state["version"]))
    assert state["state"] == "running"
    reward_step = _current_step(state)
    assert reward_step["kind"] == "reward"

    meta = reward_step["metadata"]
    assert meta["reward_kind"] == "joke"
    setup, punchline = meta["setup"], meta["punchline"]
    assert setup == _JOKE_SETUP
    assert meta["spoken_audio_setup_url"] == clip_url(voice, setup)
    assert meta["spoken_audio_punchline_url"] == clip_url(voice, punchline)
    # Joke steps get the pair, NOT the plain-step key.
    assert "spoken_audio_url" not in meta

    # Persisted row agrees with the wire (audit-wire-shape rule).
    persisted = _db_step_metadata(db_path, activity_id)
    reward_seq = max(persisted)
    assert persisted[reward_seq]["spoken_audio_setup_url"] == meta["spoken_audio_setup_url"]

    # Both clips render; the setup clip serves through the app.
    _wait_for_files([clip_path(voice, setup), clip_path(voice, punchline)])
    resp = tts_client.get(cast("str", meta["spoken_audio_setup_url"]))
    assert resp.status_code == 200
    assert resp.content[:4] == b"RIFF"


# =====================================================================
# 5. Parent insert-joke / insert-song
# =====================================================================


def test_parent_insert_joke_carries_pair_and_song_carries_none(
    tts_env: Path,
    tts_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _clear_corpora: None,
) -> None:
    _stage_templates(tmp_path, monkeypatch, _TEMPLATE_REWARD_WALK)
    _stage_joke_corpus(tts_env)
    _stage_song_corpus(tts_env)
    voice = DEFAULT_NEURAL_VOICE

    proposed = _propose(tts_client, parent_headers)
    activity_id = proposed["id"]
    state = _approve(tts_client, parent_headers, activity_id, proposed["version"])
    state = _advance(tts_client, parent_headers, activity_id, state["version"])
    assert state["state"] == "running"

    # --- insert-joke → setup/punchline clip pair ----------------------
    resp = tts_client.post(
        f"/api/activities/{activity_id}/insert-joke",
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert resp.status_code == 200, resp.text
    state = cast("dict[str, Any]", resp.json())
    joke_step = _current_step(state)
    assert joke_step["kind"] == "joke"
    joke_meta = joke_step["metadata"]
    # Interjection contract: body IS the setup; punchline in metadata.
    assert joke_meta["spoken_audio_setup_url"] == clip_url(voice, joke_step["body"])
    assert joke_meta["spoken_audio_punchline_url"] == clip_url(voice, joke_meta["punchline"])
    _wait_for_files(
        [
            clip_path(voice, joke_step["body"]),
            clip_path(voice, cast("str", joke_meta["punchline"])),
        ]
    )

    # --- insert-song → NO spoken keys, no synth of the title ----------
    resp = tts_client.post(
        f"/api/activities/{activity_id}/insert-song",
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert resp.status_code == 200, resp.text
    state = cast("dict[str, Any]", resp.json())
    song_step = _current_step(state)
    assert song_step["kind"] == "song"
    song_meta = song_step["metadata"]
    assert song_meta["audio_url"].endswith(".mp3")  # songs keep their mp3
    assert not any(key.startswith("spoken_") for key in song_meta), song_meta
    assert not clip_path(voice, song_step["body"]).exists()


# =====================================================================
# 6. Approve latency: enqueue is non-blocking even with a wedged synth
# =====================================================================


def test_approve_returns_while_synth_is_blocked(
    tts_env: Path,
    tts_client: TestClient,
    parent_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deterministic non-blocking proof: synth is parked on an Event
    only THIS test releases — if approve's enqueue path waited on the
    render (or on queue capacity), the request could never return and
    the test would time out rather than flake."""
    _stage_templates(tmp_path, monkeypatch, _TEMPLATE_TTS_PROBE)
    release = threading.Event()
    in_synth = threading.Event()

    def wedged_synth(text: str, voice: str) -> bytes:
        from toybox.tts.engine import _stub_wav_bytes

        in_synth.set()
        assert release.wait(timeout=15.0)
        return _stub_wav_bytes(text, voice)

    monkeypatch.setattr(tts_worker_mod, "synthesize", wedged_synth)

    try:
        proposed = _propose(tts_client, parent_headers)
        approved = _approve(tts_client, parent_headers, proposed["id"], proposed["version"])
        # Approve RETURNED with the full wire shape while the worker is
        # provably wedged inside the first render.
        assert in_synth.wait(timeout=5.0), "worker never picked up the first job"
        assert not release.is_set()
        assert approved["steps"][0]["metadata"]["spoken_audio_url"] == clip_url(
            DEFAULT_NEURAL_VOICE, _S1_TEXT
        )
        worker = get_tts_worker()
        assert worker is not None
        # Remaining jobs should still be queued. The threadsafe puts
        # from the handler thread may still be marshalling onto the
        # worker's loop, so poll (bounded) instead of reading qsize
        # once across threads.
        deadline = time.monotonic() + 5.0
        while worker.queue_size == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.queue_size > 0, "remaining jobs should still be queued"
    finally:
        release.set()

    # After release the backlog drains normally.
    _wait_for_files([clip_path(DEFAULT_NEURAL_VOICE, _S1_TEXT)])


# =====================================================================
# 7. Persona voice threads into the URLs (default when absent)
# =====================================================================


def test_persona_neural_voice_threads_into_clip_urls(
    tts_env: Path,
    tts_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stage_templates(tmp_path, monkeypatch, _TEMPLATE_TTS_PROBE)
    _seed_wizard_persona(db_path)

    proposed = _propose(tts_client, parent_headers)
    assert proposed["persona_id"] == "wizard"
    activity_id = proposed["id"]
    approved = _approve(tts_client, parent_headers, activity_id, proposed["version"])

    # Every URL rides the persona's cast voice, not the default.
    url = approved["steps"][0]["metadata"]["spoken_audio_url"]
    assert url == clip_url("am_michael", _S1_TEXT)
    assert url.startswith(f"{TTS_AUDIO_URL_PREFIX}/am_michael/")

    _advance(tts_client, parent_headers, activity_id, approved["version"])
    persisted = _db_step_metadata(db_path, activity_id)
    assert persisted[1]["spoken_audio_url"] == clip_url("am_michael", _S1_TEXT)

    # The clip lands under the persona voice's cache dir.
    _wait_for_files([clip_path("am_michael", _S1_TEXT)])
    resp = tts_client.get(cast("str", persisted[1]["spoken_audio_url"]))
    assert resp.status_code == 200
    assert resp.content[:4] == b"RIFF"


# =====================================================================
# 8. Corrupt / unsafe persona voice degrades to the default — never 500
# =====================================================================
#
# ``VoiceProfile.neural_voice`` is only length-constrained (1-64 chars,
# no pattern), so a corrupt library JSON like ``"../evil"`` parses
# fine, rides the persona envelope, and reaches Z4's URL derivation.
# ``_neural_voice_from_summary``'s ``is_safe_voice_id`` check is the
# ONLY guard between that string and ``clip_url`` raising ValueError →
# a 500 on approve. These tests drive the guard through the production
# caller for the unsafe shapes AND a JSON-tolerance branch.


@pytest.mark.parametrize(
    "voice_profile_json",
    [
        # Path traversal — the raw ValueError-in-clip_url risk.
        '{"rate": 0.9, "pitch": 0.7, "neural_voice": "../evil"}',
        # Wrong case — pydantic-valid but unsafe as a path/URL segment.
        '{"rate": 0.9, "pitch": 0.7, "neural_voice": "AM_MICHAEL"}',
        # voice_profile is not a dict at all (Z1 decode degrades it to
        # null on the wire; Z4's non-dict tolerance branch kicks in).
        '"not-a-dict"',
    ],
    ids=["traversal", "uppercase", "profile-not-a-dict"],
)
def test_corrupt_persona_voice_degrades_to_default_not_500(
    tts_env: Path,
    tts_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    voice_profile_json: str,
) -> None:
    _stage_templates(tmp_path, monkeypatch, _TEMPLATE_TTS_PROBE)
    _seed_wizard_persona(db_path, voice_profile=voice_profile_json)

    proposed = _propose(tts_client, parent_headers)
    assert proposed["persona_id"] == "wizard"
    activity_id = proposed["id"]

    # Approve must succeed (200, asserted inside the helper) — a
    # corrupt persona voice must never 500 the mutation...
    approved = _approve(tts_client, parent_headers, activity_id, proposed["version"])

    # ...and every stamped URL falls back to the DEFAULT voice, both
    # on the preview wire and in the persisted rows.
    url = approved["steps"][0]["metadata"]["spoken_audio_url"]
    assert url == clip_url(DEFAULT_NEURAL_VOICE, _S1_TEXT)
    assert url.startswith(f"{TTS_AUDIO_URL_PREFIX}/{DEFAULT_NEURAL_VOICE}/")

    _advance(tts_client, parent_headers, activity_id, approved["version"])
    persisted = _db_step_metadata(db_path, activity_id)
    assert persisted[1]["spoken_audio_url"] == clip_url(DEFAULT_NEURAL_VOICE, _S1_TEXT)

    # The clip renders under the DEFAULT voice's cache dir (nothing is
    # ever written under a traversal-shaped directory — ``tts/../evil``
    # would land at the data root).
    _wait_for_files([clip_path(DEFAULT_NEURAL_VOICE, _S1_TEXT)])
    assert not (tts_env / "evil").exists()
    assert not (tts_env / "tts" / "evil").exists()
    resp = tts_client.get(cast("str", persisted[1]["spoken_audio_url"]))
    assert resp.status_code == 200
    assert resp.content[:4] == b"RIFF"
