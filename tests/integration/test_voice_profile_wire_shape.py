"""Phase Z Step Z1 — ``voice_profile`` wire-shape integration tests.

The canonical wire-shape suite for the persona voice envelope, per
``.claude/rules/code-quality.md`` § "Audit wire shape when storage
representation changes". ``voice_profile`` is authored in the persona
library and persisted in ``personas.voice_profile`` (JSON TEXT,
migration 0014) but pre-Z1 it never rode the wire on ANY of the three
persona-envelope paths:

1. **Random pick** — ``_pick_random_library_persona`` SELECTed only
   ``id, display_name, archetype, avatar_image_path``.
2. **Pinned persona** — ``body.persona_id`` left ``persona_meta = None``
   entirely (no ``metadata["persona"]`` at all — the known
   letter-avatar-fallback bug).
3. **Listening trigger** — ``main._persist_dispatcher_activity`` built a
   display_name-only dict consumed only by ``_build_persona_reasoning``
   and never wrote the envelope.

Each test here drives the PRODUCTION caller (FastAPI TestClient propose
routes / the dispatcher persistence helper) and asserts
``metadata.persona.voice_profile`` arrives as a JSON OBJECT with numeric
``rate``/``pitch`` — NOT the raw persisted JSON string. The distinction
is load-bearing: the kiosk's typeof-number guard in
``frontend/src/child/persona-voice.ts`` silently rejects a string and
falls back to the default voice, so a producer regression to the string
shape would be invisible to unit tests on either side.

Reviewer note (code-quality.md): treat any future diff that RELAXES
these envelope assertions as suspect.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
from toybox.ws.topics import Topic

# ---------------------------------------------------------------------------
# Persona seeding
# ---------------------------------------------------------------------------

_INSERT_PERSONA_SQL = (
    "INSERT INTO personas "
    "(id, display_name, archetype, system_prompt, avatar_image_path, source, "
    " created_at, voice_profile) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

# Mirrors the shipped wizard library persona (rate 0.9 / pitch 0.7) so the
# fixture exercises realistic non-default values that can't be confused
# with the kiosk's ``{rate: 1.0, pitch: 1.0}`` default.
_WIZARD_VOICE_JSON = '{"rate": 0.9, "pitch": 0.7}'
_PRINCESS_VOICE_JSON = '{"rate": 1.0, "pitch": 1.4, "voice_name": "Samantha"}'


def _seed_persona(
    db_path: Path,
    *,
    persona_id: str,
    display_name: str,
    voice_profile: str | None,
    archetype: str | None = "wizard",
    avatar_image_path: str | None = "avatars/wizard.png",
    source: str = "library",
) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                _INSERT_PERSONA_SQL,
                (
                    persona_id,
                    display_name,
                    archetype,
                    f"system prompt for {persona_id}",
                    avatar_image_path,
                    source,
                    "2026-07-03T00:00:00Z",
                    voice_profile,
                ),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Envelope assertions (shared so every path pins the SAME shape)
# ---------------------------------------------------------------------------


def _assert_full_persona_envelope(
    persona_meta: Any,
    *,
    persona_id: str,
    display_name: str,
    expected_rate: float,
    expected_pitch: float,
    surface: str,
) -> None:
    """Assert the complete Z1 persona envelope shape on one wire surface."""
    assert isinstance(persona_meta, dict), (
        f"[{surface}] metadata.persona must be an object; got {persona_meta!r}"
    )
    assert persona_meta.get("id") == persona_id, persona_meta
    assert persona_meta.get("display_name") == display_name, persona_meta
    # The pinned path pre-Z1 emitted NO envelope, so the avatar path is
    # part of the fix surface (letter-avatar fallback) — pin it too.
    assert "avatar_image_path" in persona_meta, persona_meta
    assert "archetype" in persona_meta, persona_meta

    vp = persona_meta.get("voice_profile")
    # CRITICAL: an object, never the raw persisted JSON string — the
    # kiosk's typeof-number guard silently rejects a string.
    assert not isinstance(vp, str), (
        f"[{surface}] voice_profile must be a decoded object, got the raw "
        f"JSON string {vp!r} — the kiosk silently falls back to the "
        "default voice on this shape"
    )
    assert isinstance(vp, dict), (
        f"[{surface}] voice_profile must be present as an object; got {vp!r}"
    )
    assert isinstance(vp.get("rate"), (int, float)) and not isinstance(vp.get("rate"), bool), vp
    assert isinstance(vp.get("pitch"), (int, float)) and not isinstance(vp.get("pitch"), bool), vp
    assert vp["rate"] == expected_rate, vp
    assert vp["pitch"] == expected_pitch, vp


def _propose(
    client: TestClient,
    parent_headers: dict[str, str],
    body: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        "/api/activities/propose",
        json=body,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


def _get_activity(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
) -> dict[str, Any]:
    response = client.get(f"/api/activities/{activity_id}", headers=parent_headers)
    assert response.status_code == 200, response.text
    return cast("dict[str, Any]", response.json())


_TEMPLATE_BODY: dict[str, Any] = {
    "intent": "request_play",
    "slot": "unicorns",
    "hour": 12,
    "seed": 17,
}


# ---------------------------------------------------------------------------
# 1. Random path (template propose): decoded voice_profile on POST response,
#    WS envelope, and REST GET read-back.
# ---------------------------------------------------------------------------


def test_random_persona_voice_profile_rides_propose_ws_and_get(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    pubsub: PubSub,
) -> None:
    """Single-persona library → deterministic random pick → the decoded
    ``voice_profile`` object appears on ALL THREE wire surfaces (propose
    response, ``activity.state`` WS envelope, REST GET read-back)."""
    _seed_persona(
        db_path,
        persona_id="wizard",
        display_name="Marvelous the Wizard",
        voice_profile=_WIZARD_VOICE_JSON,
    )

    sub = pubsub.subscribe([Topic.activity_state])
    try:
        proposed = _propose(client, parent_headers, _TEMPLATE_BODY)
        envelope = sub.get_nowait()
    finally:
        sub.close()

    assert proposed["persona_id"] == "wizard"
    _assert_full_persona_envelope(
        proposed.get("metadata", {}).get("persona"),
        persona_id="wizard",
        display_name="Marvelous the Wizard",
        expected_rate=0.9,
        expected_pitch=0.7,
        surface="propose response",
    )
    # The wizard fixture has no voice_name — exclude_none must drop the
    # key rather than send ``voice_name: null``.
    assert "voice_name" not in proposed["metadata"]["persona"]["voice_profile"]

    # WS surface: the kiosk consumes the ``activity.state`` envelope, so
    # the persona envelope must survive ``_emit_state``'s field stripping.
    assert envelope.topic is Topic.activity_state
    assert envelope.payload["id"] == proposed["id"]
    _assert_full_persona_envelope(
        envelope.payload.get("metadata", {}).get("persona"),
        persona_id="wizard",
        display_name="Marvelous the Wizard",
        expected_rate=0.9,
        expected_pitch=0.7,
        surface="WS activity.state",
    )

    # REST GET read-back: the envelope round-trips through the persisted
    # ``activities.summary`` JSON — a producer that only decorated the
    # in-memory response would fail here.
    fetched = _get_activity(client, parent_headers, proposed["id"])
    _assert_full_persona_envelope(
        fetched.get("metadata", {}).get("persona"),
        persona_id="wizard",
        display_name="Marvelous the Wizard",
        expected_rate=0.9,
        expected_pitch=0.7,
        surface="REST GET",
    )


# ---------------------------------------------------------------------------
# 2. Pinned path — all 3 propose flows. Pre-Z1: persona_meta stayed None
#    (no envelope at all) whenever body.persona_id was supplied.
# ---------------------------------------------------------------------------


def test_pinned_persona_template_path_hydrates_full_envelope(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Template propose with ``persona_id`` pinned → full envelope
    (id + avatar + decoded voice_profile), same shape as the random pick."""
    # Seed TWO personas so a still-random pick would be flaky — the
    # pinned id must be honoured deterministically.
    _seed_persona(
        db_path,
        persona_id="wizard",
        display_name="Marvelous the Wizard",
        voice_profile=_WIZARD_VOICE_JSON,
    )
    _seed_persona(
        db_path,
        persona_id="princess",
        display_name="Princess Lyra",
        voice_profile=_PRINCESS_VOICE_JSON,
        archetype="princess",
        avatar_image_path="avatars/princess.png",
    )

    proposed = _propose(
        client,
        parent_headers,
        {**_TEMPLATE_BODY, "persona_id": "princess"},
    )
    assert proposed["persona_id"] == "princess"
    persona_meta = proposed.get("metadata", {}).get("persona")
    _assert_full_persona_envelope(
        persona_meta,
        persona_id="princess",
        display_name="Princess Lyra",
        expected_rate=1.0,
        expected_pitch=1.4,
        surface="pinned template propose",
    )
    assert persona_meta["avatar_image_path"] == "avatars/princess.png"
    # voice_name is set on this fixture — it must ride through decoded.
    assert persona_meta["voice_profile"].get("voice_name") == "Samantha"


def test_pinned_persona_standalone_path_hydrates_full_envelope(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Standalone (request_joke) propose with ``persona_id`` pinned →
    full envelope. Mirrors the K13 corpus staging pattern."""
    from toybox.activities import joke_corpus

    jokes_dir = tmp_path / "jokes"
    jokes_dir.mkdir(parents=True, exist_ok=True)
    (jokes_dir / "jokes.json").write_text(
        json.dumps(
            [
                {
                    "id": "z1-stub-joke",
                    "setup": "Why did the Z1 envelope cross the wire?",
                    "punchline": "To reach the kiosk end-to-end.",
                    "theme": "silly",
                    "optional_toy_slot": False,
                    "age_band": "3-5",
                    "persona_compat": ["all"],
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    joke_corpus.clear_joke_cache()
    try:
        _seed_persona(
            db_path,
            persona_id="wizard",
            display_name="Marvelous the Wizard",
            voice_profile=_WIZARD_VOICE_JSON,
        )
        proposed = _propose(
            client,
            parent_headers,
            {
                "intent": "request_joke",
                "slot": None,
                "hour": 12,
                "seed": 17,
                "persona_id": "wizard",
            },
        )
    finally:
        joke_corpus.clear_joke_cache()

    assert proposed["state"] == "proposed", proposed
    assert proposed["persona_id"] == "wizard"
    _assert_full_persona_envelope(
        proposed.get("metadata", {}).get("persona"),
        persona_id="wizard",
        display_name="Marvelous the Wizard",
        expected_rate=0.9,
        expected_pitch=0.7,
        surface="pinned standalone propose",
    )


def test_pinned_persona_adventure_path_hydrates_full_envelope(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Adventure propose (offline beat 0) with ``persona_id`` pinned →
    full envelope."""
    _seed_persona(
        db_path,
        persona_id="wizard",
        display_name="Marvelous the Wizard",
        voice_profile=_WIZARD_VOICE_JSON,
    )
    proposed = _propose(
        client,
        parent_headers,
        {
            "intent": "request_play",
            "slot": "freeplay",
            "hour": 12,
            "seed": 99,
            "adventure": True,
            "persona_id": "wizard",
        },
    )
    assert proposed["persona_id"] == "wizard"
    _assert_full_persona_envelope(
        proposed.get("metadata", {}).get("persona"),
        persona_id="wizard",
        display_name="Marvelous the Wizard",
        expected_rate=0.9,
        expected_pitch=0.7,
        surface="pinned adventure propose",
    )


# NOTE: a pinned persona_id with NO matching row is rejected by the
# ``activities.persona_id`` FOREIGN KEY at INSERT time — pre-existing
# (pre-Z1) behavior that Z1 does not change. The hydration helper's
# graceful ``None`` for an absent row is pinned directly in
# ``test_hydrate_helper_row_shape_direct`` below.


# ---------------------------------------------------------------------------
# 3. NULL / invalid voice_profile degradation. The kiosk treats
#    ``voice_profile: null`` as "system default" (getVoiceProfile), so both
#    cases must produce an explicit null — never a string, never a 500.
# ---------------------------------------------------------------------------


def test_null_voice_profile_rides_as_explicit_null(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    _seed_persona(
        db_path,
        persona_id="muted",
        display_name="Muted Persona",
        voice_profile=None,
    )
    proposed = _propose(client, parent_headers, _TEMPLATE_BODY)
    persona_meta = proposed.get("metadata", {}).get("persona")
    assert isinstance(persona_meta, dict), proposed.get("metadata")
    assert "voice_profile" in persona_meta, persona_meta
    assert persona_meta["voice_profile"] is None, persona_meta


def test_invalid_voice_profile_json_degrades_to_null_not_500(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Corrupt persisted JSON (not-JSON garbage AND schema-invalid
    values) must degrade to ``voice_profile: null`` — the propose path
    never 500s on bad catalog data."""
    _seed_persona(
        db_path,
        persona_id="corrupt",
        display_name="Corrupt Persona",
        voice_profile="not-even-json{",
    )
    proposed = _propose(client, parent_headers, _TEMPLATE_BODY)
    persona_meta = proposed.get("metadata", {}).get("persona")
    assert isinstance(persona_meta, dict)
    assert persona_meta["voice_profile"] is None, persona_meta

    # Schema-invalid (rate out of the pydantic [0.5, 2.0] bounds) via the
    # pinned path — same degradation contract.
    _seed_persona(
        db_path,
        persona_id="out-of-range",
        display_name="Out Of Range",
        voice_profile='{"rate": 99.0, "pitch": 1.0}',
    )
    proposed2 = _propose(
        client,
        parent_headers,
        {**_TEMPLATE_BODY, "seed": 23, "persona_id": "out-of-range"},
    )
    persona_meta2 = proposed2.get("metadata", {}).get("persona")
    assert isinstance(persona_meta2, dict)
    assert persona_meta2["voice_profile"] is None, persona_meta2


# ---------------------------------------------------------------------------
# 4. Listening-trigger path: ``_persist_dispatcher_activity`` now writes the
#    full envelope into activity metadata (pre-Z1 it wrote none at all).
# ---------------------------------------------------------------------------


def _dispatcher_activity(persona_id: str | None) -> Any:
    from toybox.activities.models import Activity, ActivityStep

    return Activity(
        # Fixed id is safe: every test gets a fresh per-test DB.
        id="00000000-0000-0000-0000-0000000000a1",
        template_id="boredom_dance",
        persona_id=persona_id,
        title="Dance break",
        steps=[
            ActivityStep(step_index=0, text="Step 1"),
            ActivityStep(step_index=1, text="Step 2"),
            ActivityStep(step_index=2, text="Step 3"),
        ],
        version=1,
        metadata={},
        toy_ids=(),
    )


def test_dispatcher_path_writes_full_persona_envelope(
    db_path: Path,
) -> None:
    """Drive ``main._persist_dispatcher_activity`` (the sole writer of the
    listening-trigger branch) and assert the persisted summary envelope
    AND the emitted WS payload both carry the full persona envelope with
    a decoded voice_profile, while ``persona_reasoning`` keeps its Phase
    N display-name behavior."""
    from toybox.main import PRODUCTION_SESSION_ID, _persist_dispatcher_activity
    from toybox.triggers.registry import Intent

    _seed_persona(
        db_path,
        persona_id="wizard",
        display_name="Marvelous the Wizard",
        voice_profile=_WIZARD_VOICE_JSON,
    )
    conn = connect(db_path, check_same_thread=False)
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (PRODUCTION_SESSION_ID, "2026-07-03T00:00:00Z"),
            )
        activity = _dispatcher_activity("wizard")
        intent = Intent(name="boredom", slot=None, pattern_id="curated-boredom-base")
        pubsub = PubSub(max_per_subscriber=8, coalesce_window_ms=0)
        sub = pubsub.subscribe([Topic.activity_state])
        try:
            _persist_dispatcher_activity(activity, intent, conn, pubsub)
            envelope = sub.get_nowait()
        finally:
            sub.close()

        # Persisted summary envelope (source of truth on read-back).
        row = conn.execute(
            "SELECT summary FROM activities WHERE id = ?",
            (activity.id,),
        ).fetchone()
        assert row is not None
        summary = json.loads(row["summary"])
        metadata = summary.get("metadata", {})
        _assert_full_persona_envelope(
            metadata.get("persona"),
            persona_id="wizard",
            display_name="Marvelous the Wizard",
            expected_rate=0.9,
            expected_pitch=0.7,
            surface="dispatcher persisted summary",
        )
        # Phase N D1 behavior preserved: reasoning synthesized from the
        # display_name, not the slug.
        reasoning = metadata.get("persona_reasoning")
        assert isinstance(reasoning, str) and "Marvelous the Wizard" in reasoning, reasoning

        # WS surface the kiosk actually consumes.
        assert envelope.payload["id"] == activity.id
        _assert_full_persona_envelope(
            envelope.payload.get("metadata", {}).get("persona"),
            persona_id="wizard",
            display_name="Marvelous the Wizard",
            expected_rate=0.9,
            expected_pitch=0.7,
            surface="dispatcher WS activity.state",
        )
    finally:
        conn.close()


def test_dispatcher_path_no_persona_still_omits_envelope(
    db_path: Path,
) -> None:
    """``persona_id=None`` on the dispatcher's Activity keeps the pre-Z1
    no-envelope shape (kiosk letter fallback) and the "matched on
    intent" reasoning sentinel."""
    from toybox.main import PRODUCTION_SESSION_ID, _persist_dispatcher_activity
    from toybox.triggers.registry import Intent

    conn = connect(db_path, check_same_thread=False)
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (PRODUCTION_SESSION_ID, "2026-07-03T00:00:00Z"),
            )
        activity = _dispatcher_activity(None)
        intent = Intent(name="boredom", slot=None, pattern_id="curated-boredom-base")
        pubsub = PubSub(max_per_subscriber=8, coalesce_window_ms=0)
        _persist_dispatcher_activity(activity, intent, conn, pubsub)

        row = conn.execute(
            "SELECT summary FROM activities WHERE id = ?",
            (activity.id,),
        ).fetchone()
        assert row is not None
        metadata = json.loads(row["summary"]).get("metadata", {})
        assert "persona" not in metadata, metadata
        assert metadata.get("persona_reasoning") == "matched on intent"
    finally:
        conn.close()


# NOTE: an Activity pinned to a persona_id with no row hits the same
# ``activities.persona_id`` FK rejection at INSERT time as the propose
# path (pre-existing, unchanged by Z1) — no dispatcher-level test for
# that shape; the hydration helper's absent-row ``None`` is pinned in
# ``test_hydrate_helper_row_shape_direct``.


# ---------------------------------------------------------------------------
# 5. sqlite3.Row access sanity for the new SELECT column (guards against a
#    driver-level surprise where the added column name doesn't resolve).
# ---------------------------------------------------------------------------


def test_hydrate_helper_row_shape_direct(db_path: Path) -> None:
    """Direct unit-ish check of ``_hydrate_persona_meta_by_id`` against a
    real migrated DB — pins the envelope keys as a set so an accidental
    key rename (id → persona_id etc.) fails loudly here rather than as a
    silent kiosk fallback."""
    from toybox.api.activities import _hydrate_persona_meta_by_id

    _seed_persona(
        db_path,
        persona_id="wizard",
        display_name="Marvelous the Wizard",
        voice_profile=_WIZARD_VOICE_JSON,
    )
    conn: sqlite3.Connection = connect(db_path)
    try:
        meta = _hydrate_persona_meta_by_id(conn, "wizard")
        assert meta is not None
        assert set(meta.keys()) == {
            "id",
            "display_name",
            "archetype",
            "avatar_image_path",
            "voice_profile",
        }
        assert meta["voice_profile"] == {"rate": 0.9, "pitch": 0.7}
        assert _hydrate_persona_meta_by_id(conn, "absent") is None
    finally:
        conn.close()
