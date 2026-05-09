"""Propose path threads the picked toy id all the way to the DB and wire.

Three-layer regression cover for the bug where the kiosk's
``ToyActionSprite`` never resolves a per-step sprite because the
propose flow drops the toy linkage:

1. The generator picks a toy by display name only — the id is lost.
2. The :class:`Activity` model has no ``toy_ids`` field.
3. ``_persist_activity`` hardcodes ``toy_ids = NULL`` in its INSERT.

This integration test exercises the full propose flow against the
TestClient and asserts both the persisted column AND the wire payload
carry the seeded toy's id. Sister read-side coverage in
``test_activities_propose_action_slot.py`` already pins the
``GET /api/activities/{id}`` round-trip path; we add the propose-time
write contract here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from toybox.db.connection import connect

_PROPOSE_BODY: dict[str, Any] = {
    "intent": "boredom",
    "slot": None,
    "hour": 12,
    "seed": 7,
}


def _seed_one_toy(db_path: Path, *, toy_id: str, display_name: str) -> None:
    """Insert a single non-archived toy directly. Mirrors the helper in
    ``tests/integration/test_generator_real_content.py`` so the test
    intent stays readable."""
    conn = connect(db_path)
    try:
        with conn:
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


def test_propose_persists_seeded_toy_id_in_activities_row(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """End-to-end: with a single toy seeded, the propose flow must persist
    that toy's id in ``activities.toy_ids`` (JSON-encoded list, NOT
    NULL, NOT empty) so the kiosk's sprite resolver finds it on read."""
    _seed_one_toy(db_path, toy_id="toy-spec-1", display_name="Bluey")

    response = client.post(
        "/api/activities/propose",
        json=_PROPOSE_BODY,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    activity_id = response.json()["id"]

    # Read the persisted row directly to pin the write path (the wire
    # response could mask a DB-side regression by populating the field
    # purely in memory).
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT toy_ids FROM activities WHERE id = ?",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    raw = row["toy_ids"]
    assert raw is not None, "activities.toy_ids must not be NULL after propose"
    decoded = json.loads(raw)
    assert decoded == ["toy-spec-1"], (
        f"persisted toy_ids should equal [seeded toy id]; got {decoded!r}"
    )


def test_propose_response_surfaces_toy_ids_on_wire(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """The propose response body and the GET activity endpoint must both
    surface the same ``toy_ids`` array. The kiosk reads this via the
    GET endpoint when re-hydrating, and the propose response is the
    immediate post-action payload."""
    _seed_one_toy(db_path, toy_id="toy-spec-2", display_name="Rex")

    propose = client.post(
        "/api/activities/propose",
        json=_PROPOSE_BODY,
        headers=parent_headers,
    )
    assert propose.status_code == 201, propose.text
    body = cast("dict[str, Any]", propose.json())
    activity_id = body["id"]

    assert body.get("toy_ids") == ["toy-spec-2"], (
        "propose response body must include toy_ids=[seeded id]; "
        f"got {body.get('toy_ids')!r}"
    )

    follow_up = client.get(
        f"/api/activities/{activity_id}",
        headers=parent_headers,
    )
    assert follow_up.status_code == 200, follow_up.text
    follow_body = cast("dict[str, Any]", follow_up.json())
    assert follow_body.get("toy_ids") == ["toy-spec-2"], (
        "GET activity must round-trip toy_ids=[seeded id]; "
        f"got {follow_body.get('toy_ids')!r}"
    )
