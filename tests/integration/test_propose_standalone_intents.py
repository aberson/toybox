"""Phase K Step K13 — standalone song/joke intents wired through propose.

This is the integration test required by ``.claude/rules/code-quality.md``
§4 "New components require an integration test through the production
caller". K10 + K11 shipped the joke/song corpora as unit-tested
modules; K13 wires the corpora into ``_do_propose`` so the trigger
phrases ``"sing me a song"`` / ``"tell me a joke"`` route through the
production POST handler and produce single-step activities with
``step.kind ∈ {"song", "joke"}`` and the per-kind ``step.metadata``
the K12 kiosk renderer dispatches on.

The test exercises ``POST /api/activities/propose`` end-to-end —
calling :func:`_do_propose_standalone` directly would re-verify K10/K11
but would NOT verify that the K13 wire-up reaches the corpus pickers
from the production handler (silent-wiring failure mode).

Surface gating: the dismissed path is asserted with both flag dimensions
(content master OFF, surface flag OFF) so a future refactor that
fuses the two conditions into one read can't silently regress one
half.

Static mount: a 6th test asserts ``GET /api/static/songs/audio/<id>.mp3``
serves the bundled file when present and 404s otherwise — the kiosk's
K12 fallback URL pattern depends on this mount existing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.activities import joke_corpus, song_corpus
from toybox.db.connection import connect
from toybox.triggers import match as trigger_match

# Per-test seed pinned so the corpus pickers land deterministically.
_PROPOSE_SONG_BODY: dict[str, Any] = {
    "intent": "request_song",
    "slot": None,
    "hour": 12,
    "seed": 17,
}
_PROPOSE_JOKE_BODY: dict[str, Any] = {
    "intent": "request_joke",
    "slot": None,
    "hour": 12,
    "seed": 17,
}


# ---------------------------------------------------------------------
# Corpus fixtures
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
    """Write a tiny placeholder mp3 so ``require_audio=True`` matches.

    The byte content doesn't matter for K13 — the picker only checks
    ``path.is_file()`` and the static mount serves whatever bytes are
    present. We write a non-empty payload so the static-mount test can
    assert content-length > 0.
    """
    full = data_root / "songs" / audio_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(b"\x00" * 32)  # tiny stub
    return full


def _good_song_entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "k13-stub-song",
        "title": "K13 Stub Song",
        "audio_path": "audio/k13-stub-song.mp3",
        "duration_seconds": 10,
        "theme": "space",
        "age_band": "3-5",
        "persona_compat": ["all"],
        "license": "CC-BY-4.0",
        "credit": "K13 test fixture",
        "lyrics": "La la la la la.",
    }
    base.update(overrides)
    return base


def _good_joke_entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "k13-stub-joke",
        "setup": "Why did the K13 chicken cross the road?",
        "punchline": "To wire the standalone surface end-to-end.",
        "theme": "silly",
        "optional_toy_slot": False,
        "age_band": "3-5",
        "persona_compat": ["all"],
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def standalone_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Point song + joke corpora at a tmp data dir with one entry each.

    The single-entry corpora guarantee the seeded picker always lands
    on the known fixture id, making the test byte-deterministic. The
    fixture's mp3 file is created so ``require_audio=True`` matches
    (the propose path passes ``require_audio=True`` to avoid kiosk 404s).

    Autouse + sets ``TOYBOX_DATA_DIR`` BEFORE the ``app`` fixture builds
    ``create_app()`` so the K13 ``/api/static/songs/audio`` mount
    captures the test's tmp data root (the StaticFiles directory is
    evaluated once at app-build time). Pytest's fixture resolution
    runs autouse fixtures with the broadest scope first; both are
    function-scoped here, but the dependency edge ``app -> db_path``
    does NOT depend on this fixture, so we rely on pytest's behaviour
    of running an autouse fixture before any test function code that
    doesn't explicitly depend on it. The ``app`` fixture transitively
    depends on the autouse ``_isolate_to_production_templates``
    fixture in conftest.py, which runs first, then ``standalone_corpus``,
    then ``app`` builds — confirmed by smoke-running pytest with
    ``--setup-show``.
    """
    # song corpus
    _write_song_manifest(tmp_path, [_good_song_entry()])
    _stub_audio(tmp_path, "audio/k13-stub-song.mp3")
    # joke corpus
    _write_joke_corpus(tmp_path, [_good_joke_entry()])
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    # Clear both module-level caches so the env override is honoured.
    song_corpus.clear_song_cache()
    joke_corpus.clear_joke_cache()
    try:
        yield tmp_path
    finally:
        song_corpus.clear_song_cache()
        joke_corpus.clear_joke_cache()


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _read_flag(db_path: Path, key: str) -> bool:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    assert row is not None, f"setting {key!r} missing — migration 0015 didn't run"
    return cast("str", row["value"]) == "true"


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


def _activity_row_count(db_path: Path) -> int:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM activities").fetchone()
    finally:
        conn.close()
    return int(row["n"])


def _read_step_kind_and_metadata(db_path: Path) -> tuple[str | None, str | None]:
    """Return (kind, metadata_json) for the single persisted step.

    code-quality.md §3 — verify the new ``activity_steps.kind`` +
    ``activity_steps.metadata_json`` columns (added in migration 0016)
    actually round-trip from ``_persist_activity`` through to disk so
    a future refactor that drops the writer can't pass silently.
    """
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT kind, metadata_json FROM activity_steps ORDER BY seq LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return (None, None)
    return (row["kind"], row["metadata_json"])


# ---------------------------------------------------------------------
# Trigger registry covers the new phrases
# ---------------------------------------------------------------------


def test_trigger_registry_classifies_sing_me_a_song_as_request_song() -> None:
    """The shipped defaults.json wires ``sing me a song`` → ``request_song``.

    Plan §"Read producers before drafting plan content" applies — the
    propose handler keys off the intent string the registry emits, so
    this end-to-end check guards against a typo / wrong intent on the
    pattern definition that would silently route the phrase to the
    template path.
    """
    intents = trigger_match("sing me a song please")
    names = {i.name for i in intents}
    assert "request_song" in names, f"expected request_song in {names}"
    # The old request_activity binding for "sing me a song" must NOT
    # also fire — that would double-dispatch.
    assert "request_activity" not in names or all(
        i.name != "request_activity" or "song" not in (i.pattern_id or "") for i in intents
    )


def test_trigger_registry_classifies_tell_me_a_joke_as_request_joke() -> None:
    intents = trigger_match("hey tell me a joke right now")
    names = {i.name for i in intents}
    assert "request_joke" in names, f"expected request_joke in {names}"


def test_lets_play_a_song_does_not_double_dispatch() -> None:
    """K13 trigger-collision guard.

    The pre-fix ``play_a_song`` regex matched "let's play a song"
    alongside the existing ``lets_play_X`` pattern, so a single utterance
    fired two intents → two propose calls → two activities. The K13
    iteration anchored the song/tune patterns with a negative lookbehind
    so they no longer fire when "let's " precedes them.
    """
    intents = trigger_match("let's play a song")
    names = {i.name for i in intents}
    assert "request_song" not in names, (
        f'"let\'s play a song" should NOT trigger request_song '
        f"(that's the lets_play_X path → request_play); got {names}"
    )
    # And the same for tunes.
    tune_intents = trigger_match("let's play a tune")
    tune_names = {i.name for i in tune_intents}
    assert "request_song" not in tune_names, (
        f'"let\'s play a tune" should NOT trigger request_song; got {tune_names}'
    )

    # But the unprefixed forms still work — that's the request_song
    # surface the kid voice flow needs.
    plain = trigger_match("play me a song")
    plain_names = {i.name for i in plain}
    assert "request_song" in plain_names, (
        f'"play me a song" should still trigger request_song; got {plain_names}'
    )


# ---------------------------------------------------------------------
# Happy path: standalone song
# ---------------------------------------------------------------------


def test_propose_request_song_returns_single_step_song_activity(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    standalone_corpus: Path,
) -> None:
    """Plan §6 K13: request_song produces a single-step activity with
    ``step.kind == "song"`` and ``step.metadata.song_id`` + ``audio_url``
    populated. Persisted activity row exists; ``activity.state`` WS
    envelope MAY be emitted (not asserted here — covered separately).
    """
    response = client.post(
        "/api/activities/propose",
        json=_PROPOSE_SONG_BODY,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())

    # Top-level shape: a real ActivityResponse, state=proposed.
    assert body["state"] == "proposed"
    assert body["intent_source"] == "request_song"
    assert body.get("reason") is None, "happy path must not carry a dismissed reason"

    # Exactly one step of kind=song with the corpus metadata.
    steps = body["steps"]
    assert isinstance(steps, list), f"expected list steps, got {type(steps).__name__}"
    assert len(steps) == 1, f"expected exactly one step, got {len(steps)}"
    step = steps[0]
    assert step["kind"] == "song"
    metadata = step.get("metadata") or {}
    assert metadata.get("song_id") == "k13-stub-song"
    audio_url = metadata.get("audio_url")
    assert isinstance(audio_url, str) and audio_url.endswith(
        "/api/static/songs/audio/k13-stub-song.mp3"
    ), f"unexpected audio_url: {audio_url!r}"
    # Step body is the song title (kiosk SongPlayer renders this).
    assert step["body"] == "K13 Stub Song"

    # The activity row landed in the DB.
    assert _activity_row_count(db_path) == 1

    # code-quality.md §3 — DB-level audit that migration 0016's new
    # columns round-trip. The wire response already asserts
    # ``step.kind`` + ``step.metadata`` above; this proves the writer
    # actually persisted them rather than reconstructing on read.
    db_kind, db_metadata_json = _read_step_kind_and_metadata(db_path)
    assert db_kind == "song", f"expected kind='song' in activity_steps, got {db_kind!r}"
    assert db_metadata_json is not None and "k13-stub-song" in db_metadata_json


# ---------------------------------------------------------------------
# Happy path: standalone joke
# ---------------------------------------------------------------------


def test_propose_request_joke_returns_single_step_joke_activity(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    standalone_corpus: Path,
) -> None:
    """Plan §6 K13: request_joke produces a single-step activity with
    ``step.kind == "joke"`` and ``step.metadata.joke_id`` +
    ``step.metadata.punchline`` populated.
    """
    response = client.post(
        "/api/activities/propose",
        json=_PROPOSE_JOKE_BODY,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())

    assert body["state"] == "proposed"
    assert body["intent_source"] == "request_joke"
    assert body.get("reason") is None

    steps = body["steps"]
    assert isinstance(steps, list) and len(steps) == 1
    step = steps[0]
    assert step["kind"] == "joke"
    metadata = step.get("metadata") or {}
    assert metadata.get("joke_id") == "k13-stub-joke"
    assert metadata.get("punchline") == "To wire the standalone surface end-to-end."

    # code-quality.md §3 — DB-level audit of migration 0016's new
    # columns (same rationale as the song-side assertion above).
    db_kind, db_metadata_json = _read_step_kind_and_metadata(db_path)
    assert db_kind == "joke", f"expected kind='joke' in activity_steps, got {db_kind!r}"
    assert db_metadata_json is not None and "k13-stub-joke" in db_metadata_json
    # Body is the setup line (kiosk JokeStep auto-speaks this first,
    # then reveals punchline after 1.5s).
    assert step["body"] == "Why did the K13 chicken cross the road?"

    assert _activity_row_count(db_path) == 1


# ---------------------------------------------------------------------
# Surface disabled: content master OFF
# ---------------------------------------------------------------------


def test_propose_request_song_with_songs_disabled_returns_dismissed(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    standalone_corpus: Path,
) -> None:
    """Plan §7: when ``songs_enabled = false``, propose returns HTTP 200
    with ``{state: "dismissed", reason: "surface_disabled"}`` and no
    activity row is persisted.

    The 200 status code (not 4xx) is the load-bearing contract — the
    kid voice flow should not surface an HTTP error for a parent-
    controlled feature flag.
    """
    _set_flag(db_path, "songs_enabled", False)
    # Sanity check the fixture: standalone surface itself is still on,
    # so the only failing gate is the content master.
    assert _read_flag(db_path, "play_standalone_enabled") is True

    response = client.post(
        "/api/activities/propose",
        json=_PROPOSE_SONG_BODY,
        headers=parent_headers,
    )
    # NOT 4xx — plan §7 pins HTTP 200 with dismissed body.
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())

    assert body["state"] == "dismissed"
    assert body["reason"] == "surface_disabled"
    assert body["version"] == 1
    assert isinstance(body["id"], str) and body["id"]
    # No DB row persisted on the dismissed path.
    assert _activity_row_count(db_path) == 0


def test_propose_request_joke_with_jokes_disabled_returns_dismissed(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    standalone_corpus: Path,
) -> None:
    _set_flag(db_path, "jokes_enabled", False)
    assert _read_flag(db_path, "play_standalone_enabled") is True

    response = client.post(
        "/api/activities/propose",
        json=_PROPOSE_JOKE_BODY,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())

    assert body["state"] == "dismissed"
    assert body["reason"] == "surface_disabled"
    assert _activity_row_count(db_path) == 0


# ---------------------------------------------------------------------
# Surface disabled: play_standalone_enabled OFF
# ---------------------------------------------------------------------


def test_propose_request_song_with_play_standalone_disabled_returns_dismissed(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    standalone_corpus: Path,
) -> None:
    """Two gates must both pass for the standalone surface. This case
    asserts the play_standalone_enabled half — without dual coverage a
    future refactor could collapse the gate to a single read.
    """
    _set_flag(db_path, "play_standalone_enabled", False)
    # Content master stays on so the only failing gate is the surface.
    assert _read_flag(db_path, "songs_enabled") is True

    response = client.post(
        "/api/activities/propose",
        json=_PROPOSE_SONG_BODY,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())

    assert body["state"] == "dismissed"
    assert body["reason"] == "surface_disabled"
    assert _activity_row_count(db_path) == 0


def test_propose_request_joke_with_play_standalone_disabled_returns_dismissed(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    standalone_corpus: Path,
) -> None:
    _set_flag(db_path, "play_standalone_enabled", False)
    assert _read_flag(db_path, "jokes_enabled") is True

    response = client.post(
        "/api/activities/propose",
        json=_PROPOSE_JOKE_BODY,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = cast("dict[str, Any]", response.json())

    assert body["state"] == "dismissed"
    assert body["reason"] == "surface_disabled"
    assert _activity_row_count(db_path) == 0


# ---------------------------------------------------------------------
# Static mount: GET /api/static/songs/audio/<id>.mp3
# ---------------------------------------------------------------------


def test_static_mount_serves_song_audio_when_present(
    client: TestClient,
    standalone_corpus: Path,
) -> None:
    """K13 adds an ``/api/static/songs/audio`` StaticFiles mount so the
    kiosk's ``SongPlayer`` fallback URL resolves. The fixture wrote a
    32-byte stub at ``data/songs/audio/k13-stub-song.mp3``; the GET
    must return 200 with the same bytes.
    """
    response = client.get("/api/static/songs/audio/k13-stub-song.mp3")
    assert response.status_code == 200, response.text
    assert response.content == b"\x00" * 32


def test_static_mount_404s_on_missing_song_audio(
    client: TestClient,
    standalone_corpus: Path,
) -> None:
    """A request for a missing mp3 must 404 rather than 500 or 200 —
    the K12 kiosk grace-period error handler relies on this shape.
    """
    response = client.get("/api/static/songs/audio/does-not-exist.mp3")
    assert response.status_code == 404
