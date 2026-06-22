"""Phase Y Step Y5 — scene_id persists on propose + serializes to scene_url.

Exercises the producer -> consumer round trip through the REAL propose endpoint:
propose -> ``activities.scene_id`` column written -> ``ActivityResponse.scene_url``
on both the propose response AND a subsequent GET (the SELECT * serializer path).

The integration conftest sandboxes templates to the 4 shipped production
templates (no ``scene_id``), and a no-child propose carries no interest tokens,
so ``resolve_scene_id`` falls through to ``DEFAULT_SCENE_ID`` — a deterministic
target for the round-trip assertion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from toybox.activities.scene_catalog import DEFAULT_SCENE_ID
from toybox.db.connection import connect

_EXPECTED_DEFAULT_URL = f"/api/static/images/scenes/{DEFAULT_SCENE_ID}.png"


def _propose(client: TestClient, parent_headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/activities/propose",
        json={"intent": "boredom", "slot": None, "hour": 12, "seed": 17},
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


def test_propose_response_carries_scene_url(
    client: TestClient, parent_headers: dict[str, str]
) -> None:
    body = _propose(client, parent_headers)
    assert body["scene_url"] == _EXPECTED_DEFAULT_URL


def test_scene_id_persisted_to_activities_row(
    client: TestClient, parent_headers: dict[str, str], db_path: Path
) -> None:
    body = _propose(client, parent_headers)
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT scene_id FROM activities WHERE id = ?", (body["id"],)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["scene_id"] == DEFAULT_SCENE_ID


def test_get_activity_round_trips_scene_url(
    client: TestClient, parent_headers: dict[str, str]
) -> None:
    body = _propose(client, parent_headers)
    # The GET path serializes from a ``SELECT *`` row — proves scene_id reaches
    # the wire through the serializer, not just the propose-time response build.
    get_resp = client.get(f"/api/activities/{body['id']}", headers=parent_headers)
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["scene_url"] == _EXPECTED_DEFAULT_URL
