"""Phase W Step W5 — boss-fight CLIMAX beat integration tests.

Exercise the FULL producer→consumer round trip (code-quality.md §4) through
the REAL propose → approve → advance handlers (NO direct-DB step injection):

* boss_fights_enabled=ON: an adventure run to the climax emits exactly ONE
  ``kind="boss_fight"`` step at the expected position (the final generated
  beat, index MAX_ADVENTURE_BEATS - 1); it casts a boss-role toy from the
  cast; resolving it advances to the reward/terminal path.
* boss_fights_enabled=OFF: no boss_fight beat appears (all adventure_beat);
  the adventure still terminates at reward (W4 behavior).
* the boss beat casts a cast member tagged big_bad_boss / boss_mini_boss;
  falls back gracefully when the cast has no boss role.
* GET/PUT settings round trip for the new flag.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.activities.adventure import MAX_ADVENTURE_BEATS
from toybox.api.activities import get_sync_ai_client
from toybox.api.auth_dep import get_auth_db
from toybox.api.boss_fights_enabled_settings import get_db as get_boss_fights_db
from toybox.core.boss_fights_enabled import get as get_boss_fights_enabled
from toybox.core.boss_fights_enabled import set as set_boss_fights_enabled
from toybox.core.game_linearity import set_game_linearity
from toybox.db.connection import connect

MAX_ADVANCE_LOOPS = 12


# ---------------------------------------------------------------------------
# Helpers.
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


def _seed_boss_toy(db_path: Path) -> str:
    """Insert one unrestricted HERO + one ``big_bad_boss``-tagged toy.

    Returns the boss toy's display name so a caller can assert the boss
    beat cast it.

    MEDIUM-1 robustness: the hero's id (``aaa_hero``) sorts BEFORE the
    boss's id (``zzz_boss``) under ``id COLLATE BINARY ASC``, so the hero is
    ``cast[0]`` AND the unrestricted toy that sorts first. The boss must
    STILL be the tagged toy (not the hero, not a sort-order coincidence) —
    this would fail if ``_select_boss_name`` either cast the hero or
    preferred the sort-first unrestricted toy over the explicit tag.
    """
    toys = [
        ("aaa_hero", "Captain Bear", None),
        ("zzz_boss", "Bowser", json.dumps(["big_bad_boss"])),
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


@pytest.fixture
def offline_client(app: FastAPI, db_path: Path) -> Iterator[TestClient]:
    app.dependency_overrides[get_sync_ai_client] = lambda: None

    # The boss-fights settings router has its OWN get_db dependency (not
    # one of the conftest-overridden activities/auth deps), so route it to
    # the test DB for the GET/PUT settings round-trip assertions.
    def _override_boss_db() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_boss_fights_db] = _override_boss_db
    # conftest's app fixture already overrides get_auth_db → db_path; keep
    # it pinned here too so the PUT's parent-scope check reads the test DB.
    app.dependency_overrides[get_auth_db] = _override_boss_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_sync_ai_client, None)
        app.dependency_overrides.pop(get_boss_fights_db, None)


def _walk_to_terminal(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    state: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Advance until the activity reaches a terminal/reward state.

    Returns ``(final_state, current_steps_seen)`` — the running list of the
    "current" step at each advance so a caller can find the boss step.
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
# PRIMARY — boss ON emits exactly one boss_fight at the climax, then reward.
# ---------------------------------------------------------------------------


def test_boss_on_emits_one_boss_fight_at_climax_then_reward(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    boss_name = _seed_boss_toy(db_path)
    _set_linearity(db_path, "nonlinear")
    _set_boss_fights(db_path, True)

    body = _propose_adventure(offline_client, parent_headers, seed=99)
    activity_id = body["id"]

    # Beat 1 (seeded at propose) is NOT the climax — it is an ordinary beat.
    first = _current_step(body)
    assert first is not None
    assert first["kind"] == "adventure_beat"

    state = _approve(offline_client, parent_headers, activity_id, body["version"])
    state = _advance(offline_client, parent_headers, activity_id, state["version"])

    state, seen = _walk_to_terminal(offline_client, parent_headers, activity_id, state)

    # Exactly ONE boss_fight step persisted (not zero, not two).
    assert _count_kind_rows(db_path, activity_id, "boss_fight") == 1
    # It sits at the climax position: seq == MAX_ADVENTURE_BEATS (1-based seq,
    # 0-based beat index MAX_ADVENTURE_BEATS - 1).
    assert _boss_seq(db_path, activity_id) == MAX_ADVENTURE_BEATS
    # There are MAX_ADVENTURE_BEATS - 1 ordinary beats + the 1 boss beat;
    # do NOT exceed the cap (no 7th beat).
    assert _count_kind_rows(db_path, activity_id, "adventure_beat") == MAX_ADVENTURE_BEATS - 1

    # The boss step was surfaced on the wire as kind=boss_fight with choices
    # (nonlinear) and named the boss-role toy.
    boss_steps = [s for s in seen if s.get("kind") == "boss_fight"]
    assert boss_steps, "boss_fight step never surfaced on the wire"
    boss_step = boss_steps[0]
    assert boss_step["choices"], "nonlinear boss beat must carry defeat choices"
    rendered = boss_step["body"] + " ".join(c["label"] for c in boss_step["choices"])
    assert boss_name in rendered, "boss beat must cast the big_bad_boss toy"

    # Resolving the boss beat advances to the reward/terminal path.
    assert state["state"] in {"completed", "ended"} or any(s.get("kind") == "reward" for s in seen)


def test_boss_off_no_boss_fight_still_terminates(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    _seed_boss_toy(db_path)
    _set_linearity(db_path, "nonlinear")
    _set_boss_fights(db_path, False)

    body = _propose_adventure(offline_client, parent_headers, seed=77)
    activity_id = body["id"]
    state = _approve(offline_client, parent_headers, activity_id, body["version"])
    state = _advance(offline_client, parent_headers, activity_id, state["version"])

    state, seen = _walk_to_terminal(offline_client, parent_headers, activity_id, state)

    # No boss beat at all; all generated beats are adventure_beat (W4).
    assert _count_kind_rows(db_path, activity_id, "boss_fight") == 0
    assert _count_kind_rows(db_path, activity_id, "adventure_beat") == MAX_ADVENTURE_BEATS
    # Still terminates at reward/terminal.
    assert state["state"] in {"completed", "ended"} or any(s.get("kind") == "reward" for s in seen)


def test_boss_on_with_no_boss_toy_falls_back_gracefully(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """No boss-role toy in the cast → boss beat still emits (generic boss)."""
    # Deliberately seed NO toys — cast is empty / generic.
    _set_linearity(db_path, "nonlinear")
    _set_boss_fights(db_path, True)

    body = _propose_adventure(offline_client, parent_headers, seed=55)
    activity_id = body["id"]
    state = _approve(offline_client, parent_headers, activity_id, body["version"])
    state = _advance(offline_client, parent_headers, activity_id, state["version"])

    _state, seen = _walk_to_terminal(offline_client, parent_headers, activity_id, state)

    assert _count_kind_rows(db_path, activity_id, "boss_fight") == 1
    boss_steps = [s for s in seen if s.get("kind") == "boss_fight"]
    assert boss_steps
    assert boss_steps[0]["body"], "boss beat body must be non-empty even with no boss toy"


def test_boss_on_linear_climax_has_no_choices(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    _seed_boss_toy(db_path)
    _set_linearity(db_path, "linear")
    _set_boss_fights(db_path, True)

    body = _propose_adventure(offline_client, parent_headers, seed=33)
    activity_id = body["id"]
    state = _approve(offline_client, parent_headers, activity_id, body["version"])
    state = _advance(offline_client, parent_headers, activity_id, state["version"])

    _state, seen = _walk_to_terminal(offline_client, parent_headers, activity_id, state)

    boss_steps = [s for s in seen if s.get("kind") == "boss_fight"]
    assert boss_steps
    assert not boss_steps[0]["choices"], "linear boss beat must carry no choices"


# ---------------------------------------------------------------------------
# Settings GET/PUT round trip for the new flag.
# ---------------------------------------------------------------------------


def test_boss_fights_setting_default_on_and_get(
    offline_client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    resp = offline_client.get("/api/settings/boss-fights-enabled")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"value": True}


def test_boss_fights_setting_put_parent_scope(
    offline_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    resp = offline_client.put(
        "/api/settings/boss-fights-enabled",
        json={"value": False},
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"value": False}

    conn = connect(db_path, check_same_thread=False)
    try:
        assert get_boss_fights_enabled(conn) is False
    finally:
        conn.close()


def test_boss_fights_setting_put_requires_parent_scope(
    offline_client: TestClient,
) -> None:
    # No parent headers → unauthorized.
    resp = offline_client.put(
        "/api/settings/boss-fights-enabled",
        json={"value": False},
    )
    assert resp.status_code in {401, 403}, resp.text


def test_boss_fights_setting_put_invalid_value_returns_422(
    offline_client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """MEDIUM-2: a non-bool body → 422.

    Mirrors the jokes_enabled / qa_grading bool-setting tests: exercises
    BOTH the pydantic ``value: bool`` coercion AND the manual
    ``ValueError → HTTPException(422)`` translation in the router.
    """
    resp = offline_client.put(
        "/api/settings/boss-fights-enabled",
        json={"value": "notabool"},
        headers=parent_headers,
    )
    assert resp.status_code == 422, resp.text


def test_boss_fights_setting_put_missing_field_returns_422(
    offline_client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Missing body field → FastAPI's default 422 (pydantic validation)."""
    resp = offline_client.put(
        "/api/settings/boss-fights-enabled",
        json={},
        headers=parent_headers,
    )
    assert resp.status_code == 422, resp.text
