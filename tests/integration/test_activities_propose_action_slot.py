"""Phase F Step F6 — full propose path carries ``action_slot`` end-to-end.

Per plan §F6 done-when #6 (and the
``feedback_buildstep_require_integration_test`` memory): an integration
test through ``_do_propose`` is mandatory. This drives the full flow:

    POST /api/activities/propose
        → offline generator emits ActivityStep with action_slot
        → _persist_activity INSERTs activity_steps with action_slot
        → _emit_state publishes Topic.activity_state envelope whose
          payload.steps[i].action_slot is set
        → REST response body's steps[i].action_slot is set
        → DB row reads back with action_slot persisted

Stays on the offline path (no Claude) so the test doesn't depend on
OAuth or network. ``slot=None`` keeps the seeded picker on the
``boredom`` always pool, which has slot-tagged steps after F6.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
from toybox.image_gen.models import ACTION_SLOTS
from toybox.ws.topics import Topic

_PROPOSE_BODY: dict[str, Any] = {
    "intent": "boredom",
    "slot": None,
    "hour": 12,
    "seed": 7,
}


def test_propose_persists_and_emits_action_slot(
    client: TestClient,
    parent_headers: dict[str, str],
    pubsub: PubSub,
    db_path: Path,
) -> None:
    """Full integration: REST → generator → DB → WS. Every step on the
    propose response has an action_slot in ACTION_SLOTS, the persisted
    row matches, and the activity.state envelope carries the same."""
    sub = pubsub.subscribe([Topic.activity_state])
    try:
        response = client.post(
            "/api/activities/propose",
            json=_PROPOSE_BODY,
            headers=parent_headers,
        )
        assert response.status_code == 201, response.text
        body = cast("dict[str, Any]", response.json())

        # --- REST contract: every step has a valid slot ----------------
        assert len(body["steps"]) == 5
        for step in body["steps"]:
            slot = step["action_slot"]
            # The seeded boredom + always-pool template hand-authors a
            # non-NULL slot for every step; if a future template change
            # leaves a NULL the kiosk render path silently drops sprites.
            assert slot is not None, (
                f"step seq={step['seq']} arrived with NULL action_slot; "
                "shipped templates must set a slot per F6 plan"
            )
            assert slot in ACTION_SLOTS, (
                f"step seq={step['seq']} action_slot={slot!r} not in vocab"
            )

        activity_id = body["id"]

        # --- DB contract: read-back through a fresh connection --------
        conn = connect(db_path, check_same_thread=False)
        try:
            rows = conn.execute(
                "SELECT seq, action_slot FROM activity_steps "
                "WHERE activity_id = ? ORDER BY seq ASC",
                (activity_id,),
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 5
        rest_by_seq = {int(s["seq"]): s["action_slot"] for s in body["steps"]}
        for r in rows:
            seq = int(r["seq"])
            persisted = r["action_slot"]
            assert persisted is not None
            assert persisted == rest_by_seq[seq], (
                f"DB action_slot for seq={seq} ({persisted!r}) does not match "
                f"REST response ({rest_by_seq[seq]!r})"
            )

        # --- WS envelope: same slot per seq -----------------------------
        envelope = sub.get_nowait()
        assert envelope.topic is Topic.activity_state
        assert envelope.payload["id"] == activity_id
        ws_steps = envelope.payload["steps"]
        assert len(ws_steps) == 5
        ws_by_seq = {int(s["seq"]): s["action_slot"] for s in ws_steps}
        assert ws_by_seq == rest_by_seq
    finally:
        sub.close()


def test_propose_action_slot_round_trips_through_get(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """The GET activity endpoint surfaces the persisted slot identically.

    Pins the read-side ``_fetch_steps`` SELECT (separate from the
    propose-time write path) so a future column rename or SELECT-list
    drift can't silently drop the field on read."""
    propose = client.post(
        "/api/activities/propose",
        json=_PROPOSE_BODY,
        headers=parent_headers,
    )
    assert propose.status_code == 201, propose.text
    activity_id = propose.json()["id"]
    propose_steps = propose.json()["steps"]

    follow_up = client.get(
        f"/api/activities/{activity_id}",
        headers=parent_headers,
    )
    assert follow_up.status_code == 200
    get_steps = follow_up.json()["steps"]
    assert len(get_steps) == 5
    for ps, gs in zip(propose_steps, get_steps, strict=True):
        assert ps["action_slot"] == gs["action_slot"]
        assert gs["action_slot"] in ACTION_SLOTS
