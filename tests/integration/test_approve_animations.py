"""Phase S Step S2: integration tests for avatar animation annotation on approve.

Verifies that:
1. A successful approve call invokes the animator and persists avatar_animation
   into activity_steps.metadata_json for the annotated step.
2. A version-conflict 409 response means the annotation was never called
   (the guard fires before the annotation).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.ai.client import StubClient
from toybox.api.activities import get_sync_ai_client

PROPOSE_BODY: dict[str, Any] = {
    "intent": "request_play",
    "slot": "unicorns",
    "hour": 12,
    "seed": 42,
}


def test_approve_persists_avatar_animations(
    app: FastAPI,
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Approve injects avatar_animation into the step's metadata.

    The animation is persisted into activity_steps.metadata_json at approve-time.
    The GET for ``approved`` state renders from the template plan (not DB rows)
    so the animation doesn't appear there yet — we advance to ``running`` first,
    which causes _fetch_steps to read from DB rows where metadata_json now lives.
    """
    # 1. Propose an activity.
    propose_resp = client.post(
        "/api/activities/propose",
        json=PROPOSE_BODY,
        headers=parent_headers,
    )
    assert propose_resp.status_code == 201, propose_resp.text
    activity_id = propose_resp.json()["id"]
    version = propose_resp.json()["version"]

    # 2. Seed StubClient with annotation JSON targeting seq=1 (the only
    #    DB-persisted step at propose time under lazy insertion).
    animation_json = '{"annotations": [{"seq": 1, "animation": "wobble"}]}'
    stub = StubClient(responses=[animation_json])
    app.dependency_overrides[get_sync_ai_client] = lambda: stub

    try:
        # 3. Approve.
        approve_resp = client.post(
            f"/api/activities/{activity_id}/approve",
            json={"child_ids": []},
            headers={**parent_headers, "If-Match-Version": str(version)},
        )
        assert approve_resp.status_code == 200, approve_resp.text
        approved_version = approve_resp.json()["version"]

        # 4. The stub was called exactly once (for the annotation).
        assert len(stub.calls) == 1
        assert stub.calls[0][0] == "complete_text_sync"

        # 5. Advance to running (approved → running is the first advance).
        # In approved state the activity auto-starts on the first advance.
        adv_resp = client.post(
            f"/api/activities/{activity_id}/advance",
            headers={**parent_headers, "If-Match-Version": str(approved_version)},
        )
        assert adv_resp.status_code == 200, adv_resp.text
        assert adv_resp.json()["state"] == "running"

        # 6. In running state, _fetch_steps reads from DB rows including
        #    the avatar_animation we wrote into metadata_json at approve-time.
        get_resp = client.get(
            f"/api/activities/{activity_id}",
            headers=parent_headers,
        )
        assert get_resp.status_code == 200, get_resp.text
        data = get_resp.json()
        steps_with_animation = [
            s
            for s in data["steps"]
            if s.get("metadata") and "avatar_animation" in s["metadata"]
        ]
        assert len(steps_with_animation) >= 1, (
            f"Expected at least one step with avatar_animation in running state; "
            f"got {data['steps']}"
        )
        assert steps_with_animation[0]["metadata"]["avatar_animation"] == "wobble"
    finally:
        del app.dependency_overrides[get_sync_ai_client]


def test_approve_version_conflict_no_annotation(
    app: FastAPI,
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """A 409 version conflict means annotation was never called."""
    # 1. Propose.
    propose_resp = client.post(
        "/api/activities/propose",
        json=PROPOSE_BODY,
        headers=parent_headers,
    )
    assert propose_resp.status_code == 201, propose_resp.text
    activity_id = propose_resp.json()["id"]

    stub = StubClient(responses=['{"annotations": [{"seq": 1, "animation": "wobble"}]}'])
    app.dependency_overrides[get_sync_ai_client] = lambda: stub

    try:
        # 2. Approve with wrong version — should 409.
        approve_resp = client.post(
            f"/api/activities/{activity_id}/approve",
            json={"child_ids": []},
            headers={**parent_headers, "If-Match-Version": "999"},
        )
        assert approve_resp.status_code == 409, approve_resp.text

        # 3. The stub should not have been called — annotation never ran.
        assert stub.calls == [], (
            f"Expected no stub calls on version conflict; got {stub.calls}"
        )
    finally:
        del app.dependency_overrides[get_sync_ai_client]


def test_approve_no_sync_client_succeeds_without_animation(
    app: FastAPI,
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """When sync_client is None (no OAuth token), approve still succeeds."""
    propose_resp = client.post(
        "/api/activities/propose",
        json=PROPOSE_BODY,
        headers=parent_headers,
    )
    assert propose_resp.status_code == 201, propose_resp.text
    activity_id = propose_resp.json()["id"]
    version = propose_resp.json()["version"]

    # Override with None — simulates no OAuth token on disk.
    app.dependency_overrides[get_sync_ai_client] = lambda: None

    try:
        approve_resp = client.post(
            f"/api/activities/{activity_id}/approve",
            json={"child_ids": []},
            headers={**parent_headers, "If-Match-Version": str(version)},
        )
        assert approve_resp.status_code == 200, approve_resp.text
        assert approve_resp.json()["state"] == "approved"
    finally:
        del app.dependency_overrides[get_sync_ai_client]
