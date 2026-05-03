"""Activity lifecycle: propose → approve → advance × 5 → completed.

Pins state-machine transitions, the wire shape of the ``activity.state``
ws envelope, and the proposed-queue cap (5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.core.pubsub import PubSub
from toybox.ws.topics import Topic

PROPOSE_BODY: dict[str, Any] = {
    "intent": "request_play",
    "slot": "unicorns",
    "hour": 12,
    "seed": 42,
}


def _propose(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/activities/propose",
        json=PROPOSE_BODY,
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


def test_propose_returns_proposed_activity(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    body = _propose(client, parent_headers)
    assert body["state"] == "proposed"
    assert body["version"] == 1
    assert len(body["steps"]) == 5


def test_propose_emits_state_envelope(
    client: TestClient,
    parent_headers: dict[str, str],
    pubsub: PubSub,
) -> None:
    sub = pubsub.subscribe([Topic.activity_state])
    try:
        body = _propose(client, parent_headers)
        envelope = sub.get_nowait()
        assert envelope.topic is Topic.activity_state
        assert envelope.payload["id"] == body["id"]
        assert envelope.payload["state"] == "proposed"
    finally:
        sub.close()


def test_approve_then_advance_to_completed(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    activity = _propose(client, parent_headers)
    activity_id = activity["id"]

    approve = client.post(
        f"/api/activities/{activity_id}/approve",
        json={"child_ids": ["c-99"]},
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert approve.status_code == 200
    assert approve.json()["state"] == "approved"
    assert approve.json()["version"] == 2

    state = approve.json()
    expected_running_versions = [3, 4, 5, 6, 7]
    for version in expected_running_versions:
        adv = client.post(
            f"/api/activities/{activity_id}/advance",
            headers={**parent_headers, "If-Match-Version": str(state["version"])},
        )
        assert adv.status_code == 200, adv.text
        state = adv.json()
        assert state["version"] == version

    # 6th advance after the 5th step was the current → finishes the activity.
    final = client.post(
        f"/api/activities/{activity_id}/advance",
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert final.status_code == 200
    assert final.json()["state"] == "completed"


def test_invalid_transition_advance_from_proposed_409(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    activity = _propose(client, parent_headers)
    response = client.post(
        f"/api/activities/{activity['id']}/advance",
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["code"] == "invalid_transition"
    assert body["current_state"] == "proposed"


def test_dismiss_proposed(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    activity = _propose(client, parent_headers)
    response = client.post(
        f"/api/activities/{activity['id']}/dismiss",
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "dismissed"


def test_regenerate_dismisses_old_returns_new(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    activity = _propose(client, parent_headers)
    response = client.post(
        f"/api/activities/{activity['id']}/regenerate",
        json={"intent": "request_play", "hour": 12, "seed": 99},
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert response.status_code == 200
    new = response.json()
    assert new["state"] == "proposed"
    assert new["id"] != activity["id"]

    old = client.get(
        f"/api/activities/{activity['id']}",
        headers=parent_headers,
    ).json()
    assert old["state"] == "dismissed"


def test_regenerate_running_succeeds_repeatedly(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Two consecutive regenerate cycles from a running activity must
    each produce a distinct new activity id. Originally regressed
    because the regenerate path used a deterministic seed
    ``(version+1)*31+7`` with no source-id mixed into the hash, so the
    second cycle collapsed to the same UUID and tripped a UNIQUE
    constraint on activities.id.
    """
    seen_ids: set[str] = set()
    for cycle in range(2):
        propose = client.post(
            "/api/activities/propose",
            json={**PROPOSE_BODY, "seed": 1000 + cycle},
            headers=parent_headers,
        )
        assert propose.status_code == 201, propose.text
        aid = propose.json()["id"]
        approve = client.post(
            f"/api/activities/{aid}/approve",
            json={},
            headers={**parent_headers, "If-Match-Version": "1"},
        )
        assert approve.status_code == 200, approve.text
        advance = client.post(
            f"/api/activities/{aid}/advance",
            headers={**parent_headers, "If-Match-Version": "2"},
        )
        assert advance.status_code == 200, advance.text
        regen = client.post(
            f"/api/activities/{aid}/regenerate",
            json={},
            headers={
                **parent_headers,
                "If-Match-Version": str(advance.json()["version"]),
            },
        )
        assert regen.status_code == 200, regen.text
        new = regen.json()
        assert new["state"] == "proposed"
        assert new["id"] not in seen_ids
        seen_ids.add(new["id"])


def test_regenerate_after_end_proposes_without_modifying_source(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """After ``end``, "skip & try another" should still produce a fresh
    proposed activity. The source ended-state must be preserved (not
    overwritten with dismissed) so analytics keep the parent-ended-early
    signal. Originally regressed because ``ended → dismissed`` is not
    allowed by the state machine and post_regenerate unconditionally
    tried that transition, returning 409 invalid_transition.
    """
    propose = client.post(
        "/api/activities/propose",
        json={**PROPOSE_BODY, "seed": 4242},
        headers=parent_headers,
    )
    aid = propose.json()["id"]
    approve = client.post(
        f"/api/activities/{aid}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    end = client.post(
        f"/api/activities/{aid}/end",
        headers={**parent_headers, "If-Match-Version": str(approve.json()["version"])},
    )
    assert end.status_code == 200
    assert end.json()["state"] == "ended"
    end_version = end.json()["version"]

    regen = client.post(
        f"/api/activities/{aid}/regenerate",
        json={},
        headers={**parent_headers, "If-Match-Version": str(end_version)},
    )
    assert regen.status_code == 200, regen.text
    new = regen.json()
    assert new["state"] == "proposed"
    assert new["id"] != aid

    # Source preserved its ended state (NOT overwritten with dismissed).
    source = client.get(f"/api/activities/{aid}", headers=parent_headers).json()
    assert source["state"] == "ended"
    assert source["version"] == end_version  # no further version bump


def test_end_running(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    activity = _propose(client, parent_headers)
    activity_id = activity["id"]
    approve = client.post(
        f"/api/activities/{activity_id}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert approve.status_code == 200
    advance = client.post(
        f"/api/activities/{activity_id}/advance",
        headers={**parent_headers, "If-Match-Version": "2"},
    )
    assert advance.status_code == 200
    assert advance.json()["state"] == "running"
    end = client.post(
        f"/api/activities/{activity_id}/end",
        headers={**parent_headers, "If-Match-Version": str(advance.json()["version"])},
    )
    assert end.status_code == 200
    assert end.json()["state"] == "ended"


def test_didnt_work_terminal(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    activity = _propose(client, parent_headers)
    approve = client.post(
        f"/api/activities/{activity['id']}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert approve.status_code == 200
    end = client.post(
        f"/api/activities/{activity['id']}/end",
        headers={**parent_headers, "If-Match-Version": "2"},
    )
    assert end.status_code == 200
    flag = client.post(
        f"/api/activities/{activity['id']}/didnt-work",
        json={"reason": "nope"},
        headers={**parent_headers, "If-Match-Version": str(end.json()["version"])},
    )
    assert flag.status_code == 200
    assert flag.json()["state"] == "didnt_work"


def test_proposed_queue_drops_oldest(
    client: TestClient,
    parent_headers: dict[str, str],
    pubsub: PubSub,
    db_path: Path,
) -> None:
    from toybox.db.connection import connect

    proposed_ids: list[str] = []
    for seed in range(1, 6):
        body = client.post(
            "/api/activities/propose",
            json={"intent": "request_play", "slot": None, "hour": 12, "seed": seed},
            headers=parent_headers,
        ).json()
        proposed_ids.append(body["id"])

    # The eviction policy orders by (created_at ASC, id ASC); ties on
    # created_at are broken by id, so compute the expected victim
    # directly from the DB rather than assuming insertion order.
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM activities WHERE state = 'proposed' "
            "ORDER BY created_at ASC, id ASC LIMIT 1"
        ).fetchall()
        expected_victim = str(rows[0]["id"])
    finally:
        conn.close()

    # Subscribe before the 6th propose so we can see the dismissal.
    sub = pubsub.subscribe([Topic.activity_state])
    try:
        sixth = client.post(
            "/api/activities/propose",
            json={"intent": "request_play", "slot": None, "hour": 12, "seed": 6},
            headers=parent_headers,
        )
        assert sixth.status_code == 201

        seen_states: list[tuple[str, str]] = []
        for _ in range(2):
            envelope = sub.get_nowait()
            seen_states.append((envelope.payload["id"], envelope.payload["state"]))

        assert (expected_victim, "dismissed") in seen_states
        ids_in_burst = [s[0] for s in seen_states]
        assert sixth.json()["id"] in ids_in_burst
    finally:
        sub.close()


@pytest.mark.parametrize(
    "missing_step",
    ["approve", "dismiss", "regenerate", "advance", "end", "didnt-work"],
)
def test_mutations_require_if_match_header(
    client: TestClient,
    parent_headers: dict[str, str],
    missing_step: str,
) -> None:
    activity = _propose(client, parent_headers)
    response = client.post(
        f"/api/activities/{activity['id']}/{missing_step}",
        json={},
        headers=parent_headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "missing_version"
