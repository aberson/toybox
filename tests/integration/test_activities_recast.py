"""Phase K K6 — ``POST /api/activities/{id}/recast`` integration tests.

Covers the recast endpoint end-to-end through the production caller:

* Happy path: propose a role-bearing template, recast, assert version
  bumps, role assignments change (or remain valid), step bodies are
  re-rendered with potentially new display names, and the persisted
  ``slot_fills_json`` carries the new role-name → display-name overlay.
* State guard: approving the activity then attempting to recast returns
  HTTP 409 with ``code: "recast_only_when_proposed"``.
* Version conflict: a stale ``If-Match-Version`` header returns HTTP
  409 with ``code: "version_conflict"`` and the current version.
* Header validation: missing ``If-Match-Version`` returns HTTP 400.
* Unknown id: returns HTTP 404.

The test mirrors the K5 propose-roles fixture wiring so the role-slot
engine is exercised through the real ``_do_propose`` path, then the
recast path follows. Code-quality.md §4 (integration test through the
production caller) requirement is satisfied for the recast endpoint.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.db.connection import connect

_PROPOSE_BODY: dict[str, Any] = {
    "intent": "boredom",
    "slot": None,
    "hour": 12,
    "seed": 99,
    "persona_id": "role_weighted_fixture",
}


@pytest.fixture
def role_template_dir(tmp_path: Path) -> Path:
    """Stage the role-required quest fixture as the only ``boredom.json``.

    Mirrors :mod:`tests.integration.test_propose_roles` so the seeded
    template picker lands on the role-required template every time.
    """
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "activities"
        / "branching"
        / "role_required_quest.json"
    )
    payload = fixture.read_text(encoding="utf-8")
    staged = tmp_path / "templates_role_required"
    staged.mkdir()
    (staged / "boredom.json").write_text(payload, encoding="utf-8")
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


def _seed_toys(db_path: Path) -> list[tuple[str, str]]:
    """Insert four toys with predictable id ordering."""
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


def _seed_role_weighted_persona(db_path: Path) -> None:
    """Insert the K5 role-weighted fixture persona row.

    Mirrors :mod:`tests.integration.test_propose_roles` — the persona's
    explicit ``role_weights`` JSON is what gives the K4 picker a
    deterministic bias.
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


def _propose_role_activity(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    role_template_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Common propose helper: seed toys + persona, point generator at the
    role fixture, POST /propose, return the parsed body.
    """
    from toybox.activities import generator

    monkeypatch.setattr(generator, "TEMPLATES_DIR", role_template_dir)
    generator.clear_template_cache()

    _seed_toys(db_path)
    _seed_role_weighted_persona(db_path)

    response = client.post(
        "/api/activities/propose",
        json=_PROPOSE_BODY,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


def test_recast_happy_path_bumps_version_and_rerolls_cast(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    role_template_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recast on a proposed role-bearing activity rerolls the cast,
    bumps the version, and re-renders persisted step bodies with the
    new role display names.
    """
    proposed = _propose_role_activity(
        client, parent_headers, db_path, role_template_dir, monkeypatch
    )
    activity_id = proposed["id"]
    original_version = proposed["version"]
    original_roles = proposed.get("roles") or {}
    original_cast_summary = proposed.get("cast_summary")
    assert set(original_roles.keys()) == {"quest_giver", "friend"}, (
        f"propose must seed both roles; got {sorted(original_roles.keys())!r}"
    )

    recast_resp = client.post(
        f"/api/activities/{activity_id}/recast",
        json={},
        headers={**parent_headers, "If-Match-Version": str(original_version)},
    )
    assert recast_resp.status_code == 200, recast_resp.text
    body = cast("dict[str, Any]", recast_resp.json())

    # ----- (a) version bumped exactly once -----
    assert body["version"] == original_version + 1, (
        f"recast must bump version once; was {original_version}, got {body['version']}"
    )
    assert body["state"] == "proposed", "recast preserves proposed state"

    # ----- (b) roles still resolved + structurally valid -----
    new_roles = body.get("roles") or {}
    assert set(new_roles.keys()) == {"quest_giver", "friend"}, (
        f"recast must keep both roles; got {sorted(new_roles.keys())!r}"
    )
    for role_name, assignment in new_roles.items():
        assert isinstance(assignment, dict)
        assert assignment.get("role_name") == role_name
        # 4 toys >> 2 required roles → both must land on real catalog toys.
        toy_id = assignment.get("toy_id")
        assert toy_id is not None, f"role {role_name!r} should have a real toy id"
        assert assignment.get("generic_descriptor") is None
        assert isinstance(assignment.get("display_name"), str)
        assert assignment["display_name"], f"role {role_name!r} must have a non-empty display_name"
    # Distinct toys per role (K4 picker contract).
    assert new_roles["friend"]["toy_id"] != new_roles["quest_giver"]["toy_id"]

    # ----- (c) cast_summary reflects the new resolved cast -----
    new_cast_summary = body.get("cast_summary")
    assert isinstance(new_cast_summary, str) and new_cast_summary, (
        f"cast_summary must be a non-empty string; got {new_cast_summary!r}"
    )
    expected_summary = (
        f"Friend: {new_roles['friend']['display_name']}, "
        f"Quest Giver: {new_roles['quest_giver']['display_name']}"
    )
    assert new_cast_summary == expected_summary

    # ----- (d) persisted step body re-renders to the new display names -----
    # ``proposed`` activities have steps[] driven by the template-plan
    # rendering path in ``_row_to_response``, but the K6 endpoint also
    # rewrites the persisted ``activity_steps`` rows. Validate both:
    # the response steps AND the DB-level body.
    steps = body.get("steps") or []
    assert steps, "recast response must include at least one step"
    rendered_text = " | ".join(str(s.get("body", "")) for s in steps)
    assert new_roles["friend"]["display_name"] in rendered_text
    assert new_roles["quest_giver"]["display_name"] in rendered_text
    assert "{quest_giver}" not in rendered_text
    assert "{friend}" not in rendered_text

    # Persisted DB row too (the lazy-inserted steps[0]).
    conn = connect(db_path, check_same_thread=False)
    try:
        step_row = conn.execute(
            "SELECT body FROM activity_steps WHERE activity_id = ? ORDER BY seq ASC LIMIT 1",
            (activity_id,),
        ).fetchone()
        slot_fills_row = conn.execute(
            "SELECT slot_fills_json FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert step_row is not None
    persisted_body = str(step_row["body"])
    assert "{quest_giver}" not in persisted_body
    assert "{friend}" not in persisted_body
    # At least one of the new role display names should appear in the
    # persisted body for step 0 of the fixture template (which uses
    # both {quest_giver} and {friend} on its first line).
    assert (
        new_roles["friend"]["display_name"] in persisted_body
        or new_roles["quest_giver"]["display_name"] in persisted_body
    ), (
        f"persisted step body should contain at least one new role display name; "
        f"got {persisted_body!r}"
    )

    # ----- (e) slot_fills_json carries new role keys -----
    assert slot_fills_row is not None
    persisted_fills = json.loads(slot_fills_row["slot_fills_json"])
    assert persisted_fills.get("friend") == new_roles["friend"]["display_name"]
    assert persisted_fills.get("quest_giver") == new_roles["quest_giver"]["display_name"]

    # ----- (f) original cast may or may not change; either way the
    #          response is internally consistent. Surface the comparison
    #          for visibility in test output but do not gate on it (a
    #          recast that lands on an identical cast is valid — the
    #          API contract is "fresh seed", not "guaranteed different").
    if new_cast_summary == original_cast_summary:
        # Same cast — fine; the seed simply collided. Recast still
        # bumped the version + re-rendered the persisted rows.
        pass


def test_recast_state_guard_when_approved_returns_409(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    role_template_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recasting an approved activity returns 409 ``recast_only_when_proposed``."""
    proposed = _propose_role_activity(
        client, parent_headers, db_path, role_template_dir, monkeypatch
    )
    activity_id = proposed["id"]
    propose_version = proposed["version"]

    # Approve to move state out of ``proposed``.
    approve = client.post(
        f"/api/activities/{activity_id}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": str(propose_version)},
    )
    assert approve.status_code == 200, approve.text
    approved_version = approve.json()["version"]

    # Recast attempt should fail with the K6 state-guard 409.
    recast = client.post(
        f"/api/activities/{activity_id}/recast",
        json={},
        headers={**parent_headers, "If-Match-Version": str(approved_version)},
    )
    assert recast.status_code == 409, recast.text
    detail = recast.json()["detail"]
    assert detail["code"] == "recast_only_when_proposed"
    assert detail["current_version"] == approved_version
    assert detail["current_state"] == "approved"


def test_recast_stale_version_returns_409_version_conflict(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    role_template_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recast with a stale If-Match-Version returns the standard
    ``version_conflict`` 409 shape.
    """
    proposed = _propose_role_activity(
        client, parent_headers, db_path, role_template_dir, monkeypatch
    )
    activity_id = proposed["id"]
    current_version = proposed["version"]

    # First recast succeeds → version bumps to current_version + 1.
    first = client.post(
        f"/api/activities/{activity_id}/recast",
        json={},
        headers={**parent_headers, "If-Match-Version": str(current_version)},
    )
    assert first.status_code == 200, first.text
    new_version = first.json()["version"]
    assert new_version == current_version + 1

    # Second recast with the stale (pre-bump) version → 409.
    second = client.post(
        f"/api/activities/{activity_id}/recast",
        json={},
        headers={**parent_headers, "If-Match-Version": str(current_version)},
    )
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "version_conflict"
    assert detail["current_version"] == new_version
    assert detail["current_state"] == "proposed"


def test_recast_missing_if_match_version_header_returns_400(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    role_template_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``If-Match-Version`` header → 400 ``missing_version``."""
    proposed = _propose_role_activity(
        client, parent_headers, db_path, role_template_dir, monkeypatch
    )
    activity_id = proposed["id"]

    resp = client.post(
        f"/api/activities/{activity_id}/recast",
        json={},
        headers=parent_headers,  # no If-Match-Version
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "missing_version"


def test_recast_unknown_activity_returns_404(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Unknown activity id → 404 ``activity_not_found``."""
    resp = client.post(
        "/api/activities/does-not-exist/recast",
        json={},
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert resp.status_code == 404, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "activity_not_found"
    assert detail["id"] == "does-not-exist"
