"""Phase W Step W6 — no-mock pipeline SMOKE GATE.

Exercises the full Phase W feature set end-to-end through the REAL
propose → approve → advance handlers (NO direct-DB injection of feature
fields; the ONLY stub is the Claude sync client where a deterministic
online path is needed). Its job is to catch producer→consumer drift
ACROSS the Phase W surfaces — not to re-test each unit (that lives in the
per-step suites: test_adventure_engine / test_boss_fight_engine /
test_activities_qa_grading / test_*_settings).

Scenarios (breadth over depth — one or two asserts per surface proving
the cross-module wiring holds):

1. ADVENTURE pipeline (W2 nonlinear + W4 generated beats + W5 boss climax)
   — once OFFLINE and once with a STUBBED-GREEN Claude client.
2. LINEAR adventure (W2 linear) — beats incl. boss carry no choices.
3. Q&A AUTO-GRADE pipeline (W3) — template-authored question+expected_answer
   auto-resolves the R3 gate WITHOUT a parent tap + WS envelope fires;
   qa_grading=off leaves the R3 parent-tap path (advance 409) intact.
4. DIALS reachable (W1) — parent_involvement + game_complexity GET/PUT
   round-trip (persist-only stubs).

Helpers/fixtures are deliberately mirrored from the per-step suites so a
breaking shape change there surfaces here too.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.activities.adventure import MAX_ADVENTURE_BEATS
from toybox.api.activities import get_sync_ai_client
from toybox.api.auth_dep import get_auth_db
from toybox.api.game_complexity_settings import get_db as get_game_complexity_db
from toybox.api.parent_involvement_settings import get_db as get_parent_involvement_db
from toybox.core.boss_fights_enabled import set as set_boss_fights_enabled
from toybox.core.game_linearity import set_game_linearity
from toybox.core.pubsub import PubSub
from toybox.core.transcript_retention import _format_ended_at_cutoff
from toybox.db.connection import connect
from toybox.ws.topics import Topic

MAX_ADVANCE_LOOPS = 12


# ---------------------------------------------------------------------------
# Settings helpers (drive the W1/W2/W5 dials directly against the test DB).
# ---------------------------------------------------------------------------


def _set_linearity(db_path: Path, value: str) -> None:
    conn = connect(db_path, check_same_thread=False)
    try:
        set_game_linearity(conn, value)
    finally:
        conn.close()


def _set_boss_fights(db_path: Path, value: bool) -> None:
    conn = connect(db_path, check_same_thread=False)
    try:
        set_boss_fights_enabled(conn, value)
    finally:
        conn.close()


def _set_qa_grading(db_path: Path, value: str) -> None:
    conn = connect(db_path, check_same_thread=False)
    try:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('qa_grading', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (value,),
            )
    finally:
        conn.close()


def _seed_boss_toy(db_path: Path) -> str:
    """Insert one unrestricted HERO + one ``big_bad_boss``-tagged toy.

    Mirrors test_boss_fight_engine._seed_boss_toy: the hero id sorts before
    the boss id, so a correct ``boss_fight`` beat must cast the *tagged*
    toy, not the sort-first cast member. Returns the boss display name.
    """
    toys = [
        ("aaa_hero", "Captain Bear", None),
        ("zzz_boss", "Bowser", '["big_bad_boss"]'),
    ]
    conn = connect(db_path, check_same_thread=False)
    try:
        with conn:
            for toy_id, display_name, allowed_json in toys:
                conn.execute(
                    "INSERT INTO toys "
                    "(id, display_name, image_path, image_hash, type, tags, "
                    " persona_id, archived, created_at, last_used_at, allowed_roles) "
                    "VALUES (?, ?, ?, ?, NULL, NULL, NULL, 0, "
                    " '2026-01-01T00:00:00Z', NULL, ?)",
                    (
                        toy_id,
                        display_name,
                        f"img/{toy_id}.png",
                        f"hash-{toy_id}",
                        allowed_json,
                    ),
                )
    finally:
        conn.close()
    return "Bowser"


def _insert_transcript(db_path: Path, text: str) -> None:
    """Insert a transcript row whose ``ended_at`` is 'now' (in the 30s window).

    Byte-identical ``ended_at`` formatting via ``_format_ended_at_cutoff`` so
    the grader's lexicographic ``ended_at >= cutoff`` comparison matches.
    """
    now_iso = _format_ended_at_cutoff(datetime.now(UTC))
    conn = connect(db_path, check_same_thread=False)
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at, mode) VALUES (?, ?, ?)",
                ("sess-w6", now_iso, 3),
            )
            conn.execute(
                "INSERT INTO transcripts (id, session_id, started_at, ended_at, text) "
                "VALUES (?, ?, ?, ?, ?)",
                ("tx-w6", "sess-w6", now_iso, now_iso, text),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# propose / approve / advance through the production handlers.
# ---------------------------------------------------------------------------


def _propose_adventure(
    client: TestClient, parent_headers: dict[str, str], *, seed: int = 99
) -> dict[str, Any]:
    resp = client.post(
        "/api/activities/propose",
        json={
            "intent": "request_play",
            "slot": "freeplay",
            "hour": 12,
            "seed": seed,
            "adventure": True,
        },
        headers=parent_headers,
    )
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


def _propose_boredom(
    client: TestClient, parent_headers: dict[str, str], *, seed: int = 42
) -> dict[str, Any]:
    resp = client.post(
        "/api/activities/propose",
        json={"intent": "boredom", "slot": None, "hour": 12, "seed": seed},
        headers=parent_headers,
    )
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


def _approve(
    client: TestClient, parent_headers: dict[str, str], activity_id: str, version: int
) -> dict[str, Any]:
    resp = client.post(
        f"/api/activities/{activity_id}/approve",
        json={},
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
    expected_status: int = 200,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if choice_index is not None:
        body["choice_index"] = choice_index
    resp = client.post(
        f"/api/activities/{activity_id}/advance",
        json=body,
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert resp.status_code == expected_status, resp.text
    return cast("dict[str, Any]", resp.json())


def _current_step(state: dict[str, Any]) -> dict[str, Any] | None:
    for step in state.get("steps", []):
        if step.get("current"):
            return cast("dict[str, Any]", step)
    return None


def _count_kind_rows(db_path: Path, activity_id: str, kind: str) -> int:
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM activity_steps WHERE activity_id = ? AND kind = ?",
            (activity_id, kind),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"])


def _boss_seq(db_path: Path, activity_id: str) -> int | None:
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT seq FROM activity_steps WHERE activity_id = ? AND kind = 'boss_fight' LIMIT 1",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else int(row["seq"])


def _walk_to_terminal(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    state: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Advance until the activity reaches a terminal/reward state.

    Returns ``(final_state, current_steps_seen)`` so callers can inspect the
    boss beat / generated beats that surfaced on the wire.
    """
    seen: list[dict[str, Any]] = []
    for _ in range(MAX_ADVANCE_LOOPS):
        current = _current_step(state)
        if current is not None:
            seen.append(current)
        ci = 0 if (current and current.get("choices")) else None
        state = _advance(client, parent_headers, activity_id, state["version"], choice_index=ci)
        new_current = _current_step(state)
        if new_current is not None:
            seen.append(new_current)
        if state["state"] in {"completed", "ended"} or (
            new_current is not None and new_current.get("kind") == "reward"
        ):
            break
    return state, seen


# ---------------------------------------------------------------------------
# Fixtures — offline client, stubbed-green client, capability-gate stubs,
# the boredom Q&A fixture template, and a settings-router DB override.
# ---------------------------------------------------------------------------


@pytest.fixture
def offline_client(app: FastAPI) -> Iterator[TestClient]:
    """TestClient with the sync AI client pinned to None (offline path)."""
    app.dependency_overrides[get_sync_ai_client] = lambda: None
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_sync_ai_client, None)


class _StubSyncClient:
    """Minimal SyncAIClient stub returning a canned adventure beat."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_text_sync(self, messages: Any, **kwargs: Any) -> Any:
        self.calls += 1
        import json

        from toybox.ai.client import AIResponse

        payload = json.dumps(
            {
                "body": f"Claude beat number {self.calls}!",
                "choices": ["Online choice A", "Online choice B"],
            }
        )
        return AIResponse(text=payload, model="stub")


@pytest.fixture
def client_with_stub_ai(app: FastAPI) -> Iterator[tuple[TestClient, _StubSyncClient]]:
    stub = _StubSyncClient()
    app.dependency_overrides[get_sync_ai_client] = lambda: stub
    try:
        with TestClient(app) as test_client:
            yield test_client, stub
    finally:
        app.dependency_overrides.pop(get_sync_ai_client, None)


async def _capable_true(*_args: Any, **_kwargs: Any) -> tuple[bool, None]:
    return True, None


@pytest.fixture
def qa_gate_dir(tmp_path: Path) -> Path:
    """Stage ``qa_gate.json`` as the only ``boredom`` template.

    The template authors ``question`` + ``expected_answer`` on its first
    step, so propose MUST land on it and carry both fields into
    ``activity_steps`` (the W3 wiring).
    """
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "activities"
        / "branching"
        / "qa_gate.json"
    )
    staged = tmp_path / "w6_qa_templates"
    staged.mkdir()
    (staged / "boredom.json").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    src_schema = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "toybox"
        / "activities"
        / "templates"
        / "_schema.json"
    )
    shutil.copy(src_schema, staged / "_schema.json")
    return staged


@pytest.fixture
def dials_client(app: FastAPI, db_path: Path) -> Iterator[TestClient]:
    """TestClient with the W1 dial settings routers pointed at the test DB.

    The parent_involvement / game_complexity routers each carry their OWN
    ``get_db`` dependency (NOT in conftest's override list), so route them —
    and the auth dep the PUT parent-scope check reads — to ``db_path``.
    """

    def _override_db() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_parent_involvement_db] = _override_db
    app.dependency_overrides[get_game_complexity_db] = _override_db
    app.dependency_overrides[get_auth_db] = _override_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_parent_involvement_db, None)
        app.dependency_overrides.pop(get_game_complexity_db, None)
        app.dependency_overrides.pop(get_auth_db, None)


# ===========================================================================
# Scenario 1 — ADVENTURE pipeline (W2 nonlinear + W4 beats + W5 boss climax).
# ===========================================================================


def test_smoke_adventure_pipeline_offline_to_boss_then_reward(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """OFFLINE nonlinear adventure with boss ON: propose → advance through the
    generated beats → ≥3 distinct adventure_beat steps, EXACTLY ONE boss_fight
    at the climax (seq == MAX_ADVENTURE_BEATS) casting the seeded boss toy,
    terminating at the reward/end path with no exception.
    """
    boss_name = _seed_boss_toy(db_path)
    _set_linearity(db_path, "nonlinear")
    _set_boss_fights(db_path, True)

    body = _propose_adventure(offline_client, parent_headers, seed=99)
    activity_id = body["id"]
    first = _current_step(body)
    assert first is not None
    assert first["kind"] == "adventure_beat"

    state = _approve(offline_client, parent_headers, activity_id, body["version"])
    state = _advance(offline_client, parent_headers, activity_id, state["version"])
    assert state["state"] == "running"

    state, seen = _walk_to_terminal(offline_client, parent_headers, activity_id, state)

    # ≥3 distinct generated adventure_beat steps (breadth proof of W4).
    distinct_beat_bodies = {
        s["body"] for s in seen if s.get("kind") == "adventure_beat" and s.get("body")
    }
    assert len(distinct_beat_bodies) >= 3, (
        f"expected ≥3 distinct adventure beats, saw {len(distinct_beat_bodies)}"
    )

    # EXACTLY ONE boss_fight at the climax (W5).
    assert _count_kind_rows(db_path, activity_id, "boss_fight") == 1
    assert _boss_seq(db_path, activity_id) == MAX_ADVENTURE_BEATS
    boss_steps = [s for s in seen if s.get("kind") == "boss_fight"]
    assert boss_steps, "boss_fight step never surfaced on the wire"
    boss_step = boss_steps[0]
    rendered = boss_step["body"] + " ".join(c["label"] for c in boss_step.get("choices") or [])
    assert boss_name in rendered, "boss beat must cast the big_bad_boss toy"

    # Terminates at reward/end with no exception.
    assert state["state"] in {"completed", "ended"} or any(s.get("kind") == "reward" for s in seen)


def test_smoke_adventure_pipeline_stubbed_green_calls_claude(
    client_with_stub_ai: tuple[TestClient, _StubSyncClient],
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ONLINE path: capability gate stubbed GREEN + a stubbed Claude client.

    Same nonlinear+boss adventure, but assert the stub WAS called (the online
    beat path is exercised) and the cycle still completes to terminal.
    """
    client, stub = client_with_stub_ai
    monkeypatch.setattr("toybox.ai.capability.is_capable", _capable_true)
    _seed_boss_toy(db_path)
    _set_linearity(db_path, "nonlinear")
    _set_boss_fights(db_path, True)

    body = _propose_adventure(client, parent_headers, seed=77)
    activity_id = body["id"]
    # Propose seeds beat 1 ONLINE — the stub fired and its body shows.
    assert stub.calls >= 1, "gate-green propose should call Claude for the seed beat"
    first = _current_step(body)
    assert first is not None
    assert first["body"].startswith("Claude beat number")

    state = _approve(client, parent_headers, activity_id, body["version"])
    state = _advance(client, parent_headers, activity_id, state["version"])
    calls_after_first_advance = stub.calls

    state, seen = _walk_to_terminal(client, parent_headers, activity_id, state)

    # The online beat path kept firing across advances.
    assert stub.calls > calls_after_first_advance, "advance should call Claude for later beats"
    # Boss climax still emitted and the cycle still completes.
    assert _count_kind_rows(db_path, activity_id, "boss_fight") == 1
    assert state["state"] in {"completed", "ended"} or any(s.get("kind") == "reward" for s in seen)


# ===========================================================================
# Scenario 2 — LINEAR adventure (W2 linear): beats incl. boss have no choices.
# ===========================================================================


def test_smoke_linear_adventure_beats_have_no_choices(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """game_linearity=linear → generated beats (incl. the boss climax) carry
    NO choices; the adventure still terminates.
    """
    _seed_boss_toy(db_path)
    _set_linearity(db_path, "linear")
    _set_boss_fights(db_path, True)

    body = _propose_adventure(offline_client, parent_headers, seed=33)
    activity_id = body["id"]
    first = _current_step(body)
    assert first is not None
    assert first["kind"] == "adventure_beat"
    assert not first["choices"], "linear adventure beat 1 must have no choices"

    state = _approve(offline_client, parent_headers, activity_id, body["version"])
    state = _advance(offline_client, parent_headers, activity_id, state["version"])

    state, seen = _walk_to_terminal(offline_client, parent_headers, activity_id, state)

    boss_steps = [s for s in seen if s.get("kind") == "boss_fight"]
    assert boss_steps, "boss beat must still surface in a linear adventure"
    assert not boss_steps[0]["choices"], "linear boss climax must carry no choices"
    # Still terminates.
    assert state["state"] in {"completed", "ended"} or any(s.get("kind") == "reward" for s in seen)


# ===========================================================================
# Scenario 3 — Q&A AUTO-GRADE pipeline (W3).
# ===========================================================================


def test_smoke_qa_auto_resolves_through_real_handlers(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    qa_gate_dir: Path,
    pubsub: PubSub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TEMPLATE-authored Q&A gate (question+expected_answer) + qa_grading=
    lenient + a matching transcript auto-resolves on advance WITHOUT a parent
    approve-question tap, and a WS activity.state envelope fires with the
    current step's question_pending=False.
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", qa_gate_dir)
    generator.clear_template_cache()
    try:
        _set_qa_grading(db_path, "lenient")

        body = _propose_boredom(offline_client, parent_headers, seed=11)
        activity_id = body["id"]

        # The wiring: the template-authored question reached the persisted row
        # (NOT injected by the test).
        conn = connect(db_path, check_same_thread=False)
        try:
            row = conn.execute(
                "SELECT question, expected_answer FROM activity_steps "
                "WHERE activity_id = ? AND seq = 1",
                (activity_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["question"] == "What colour is the sky on a clear day?"
        assert row["expected_answer"] == "blue"

        state = _approve(offline_client, parent_headers, activity_id, body["version"])
        state = _advance(offline_client, parent_headers, activity_id, state["version"])
        assert state["state"] == "running"

        # The kid answers out loud; the transcript lands in the 30s window.
        _insert_transcript(db_path, "I think the sky is blue")

        sub = pubsub.subscribe([Topic.activity_state])
        try:
            after = _advance(offline_client, parent_headers, activity_id, state["version"])
            # Auto-resolve: advance proceeded (no 409) and bumped version twice
            # (gate resolve + advance transition) WITHOUT a parent tap.
            assert after["state"] in {"running", "completed"}
            assert after["version"] >= state["version"] + 2

            saw_unhidden = False
            while True:
                try:
                    envelope = sub.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if envelope.topic is not Topic.activity_state:
                    continue
                if envelope.payload.get("id") != activity_id:
                    continue
                for s in envelope.payload.get("steps", []):
                    if s.get("current") and s.get("question_pending") is False:
                        saw_unhidden = True
            assert saw_unhidden, (
                "auto-grade did not emit an activity.state envelope with a current "
                "step having question_pending=False"
            )
        finally:
            sub.close()

        # question_approved persisted as 1 on the gated step.
        conn = connect(db_path, check_same_thread=False)
        try:
            approved = conn.execute(
                "SELECT question_approved FROM activity_steps "
                "WHERE activity_id = ? AND question = 'What colour is the sky on a clear day?'",
                (activity_id,),
            ).fetchone()
        finally:
            conn.close()
        assert approved is not None
        assert int(approved["question_approved"]) == 1
    finally:
        generator.clear_template_cache()


def test_smoke_qa_off_keeps_r3_parent_tap_path(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    qa_gate_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """qa_grading=off → advance 409s even with a matching transcript (the R3
    parent-tap path is intact). Same template-authored gate, no auto-grade.
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", qa_gate_dir)
    generator.clear_template_cache()
    try:
        _set_qa_grading(db_path, "off")

        body = _propose_boredom(offline_client, parent_headers, seed=11)
        activity_id = body["id"]
        state = _approve(offline_client, parent_headers, activity_id, body["version"])
        state = _advance(offline_client, parent_headers, activity_id, state["version"])
        assert state["state"] == "running"

        _insert_transcript(db_path, "definitely blue")

        # Grading off short-circuits auto-grade → advance still blocks (409).
        blocked = _advance(
            offline_client, parent_headers, activity_id, state["version"], expected_status=409
        )
        assert blocked["detail"]["code"] == "question_pending"
    finally:
        generator.clear_template_cache()


# ===========================================================================
# Scenario 4 — W1 DIALS reachable (persist-only stubs).
# ===========================================================================


def test_smoke_w1_dials_round_trip(
    dials_client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """parent_involvement + game_complexity GET/PUT round-trip (W1 stubs —
    confirm they persist; no behavior).
    """
    for setting in ("parent-involvement", "game-complexity"):
        # Fresh migrated DB seeds both at "medium".
        get_resp = dials_client.get(f"/api/settings/{setting}")
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json() == {"value": "medium"}

        put_resp = dials_client.put(
            f"/api/settings/{setting}",
            json={"value": "high"},
            headers=parent_headers,
        )
        assert put_resp.status_code == 200, put_resp.text
        assert put_resp.json() == {"value": "high"}

        # Read it back — the PUT persisted.
        reread = dials_client.get(f"/api/settings/{setting}")
        assert reread.status_code == 200, reread.text
        assert reread.json() == {"value": "high"}
