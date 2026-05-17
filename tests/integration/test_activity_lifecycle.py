"""Activity lifecycle: propose → approve → advance × 5 → completed.

Pins state-machine transitions, the wire shape of the ``activity.state``
ws envelope, and the proposed-queue cap (5).

Phase G G2 note: post-G2 the propose flow inserts ONLY ``steps[0]``
into ``activity_steps`` (lazy insertion). G3 ships the lazy-advance
handler that inserts subsequent steps as the kid progresses. To
preserve coverage of multi-step state transitions in this lifecycle
suite (which predates the lazy split), tests that exercise advance
through 5 steps backfill the legacy 5-row shape via
``backfill_legacy_steps`` — this simulates the in-flight pre-G2
activity that the migration explicitly preserves and exercises the
existing ``post_advance`` linear-fall-through path. New
single-step lazy semantics are covered by dedicated unit tests in
``tests/unit/activities/test_generator.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.lazy_insert import backfill_legacy_steps
from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
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


def _propose_with_legacy_5_rows(
    client: TestClient,
    headers: dict[str, str],
    db_path: Path,
) -> dict[str, Any]:
    """Propose, then backfill steps 2..5 directly so the resulting row
    set matches the pre-G2 in-flight activity shape.

    Returns the freshly re-fetched activity body so callers see the
    full 5-step list (the propose response itself only carries
    ``steps[0]`` per the G2 lazy-insertion contract).
    """
    proposed = _propose(client, headers)
    conn = connect(db_path, check_same_thread=False)
    try:
        backfill_legacy_steps(conn, proposed["id"])
    finally:
        conn.close()
    refetched = client.get(f"/api/activities/{proposed['id']}", headers=headers)
    assert refetched.status_code == 200, refetched.text
    return cast("dict[str, Any]", refetched.json())


def test_propose_returns_proposed_activity(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Phase G G2.5: propose response carries the full template step plan.

    Pre-G2 propose returned all 5 template steps because the DB had all
    5 rows pre-seeded. G2 switched to lazy insertion (only ``steps[0]``
    in the DB at creation) which narrowed the propose response to 1
    step — breaking the parent dashboard's review UX. G2.5 restores the
    full-plan response for proposed/approved activities by rendering
    from the template + persisted slot fills, while activity_steps DB
    rows remain lazy-inserted (G3's advance handler INSERTs the rest as
    the kid plays).
    """
    body = _propose(client, parent_headers)
    assert body["state"] == "proposed"
    assert body["version"] == 1
    # Full template plan: 5 steps for linear templates.
    assert len(body["steps"]) == 5
    # steps[0] is current; the rest are previewed but not yet active.
    assert body["steps"][0]["seq"] == 1
    assert body["steps"][0]["current"] is True
    for s in body["steps"][1:]:
        assert s["current"] is False


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
        # Phase J J5: ``created_at`` must be in the WS payload so the
        # parent UI can compute TTL fade on a freshly-pushed row without
        # an extra REST round-trip. Asserted here (rather than in a
        # standalone test) because every propose path funnels through
        # the same ``_emit_state`` helper — a regression that dropped
        # the field from the model_dump would surface here.
        assert "created_at" in envelope.payload
        assert envelope.payload["created_at"] == body["created_at"]
    finally:
        sub.close()


def test_approve_then_advance_to_completed(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Phase G regression: a pre-G2 in-flight 5-row activity advances
    proposed → approved → running × 5 → completed unchanged.

    Phase G G2 mandates: "old activities (pre-G2 fixture rows with
    empty ``slot_fills_json``) still advance correctly". This test
    backfills the legacy 5-row shape on top of the propose
    response so the existing ``post_advance`` linear-fall-through
    handler walks the full sequence — exactly the path an
    in-flight activity at upgrade time exercises.
    """
    activity = _propose_with_legacy_5_rows(client, parent_headers, db_path)
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

    # 6th advance after the 5th step was the current → terminal
    # advance. Phase L two-phase: if a reward fires the state stays
    # running (Phase 1) and a 7th advance flips to completed (Phase
    # 2); if no reward fires the state goes to completed in this one
    # advance (legacy single-advance path).
    final = client.post(
        f"/api/activities/{activity_id}/advance",
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert final.status_code == 200
    final_state = final.json()
    if final_state["state"] == "running":
        final = client.post(
            f"/api/activities/{activity_id}/advance",
            headers={**parent_headers, "If-Match-Version": str(final_state["version"])},
        )
        assert final.status_code == 200, final.text
        final_state = final.json()
    assert final_state["state"] == "completed"


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


def _approve_and_advance_to_seq(
    client: TestClient,
    parent_headers: dict[str, str],
    *,
    target_seq: int,
    db_path: Path,
) -> dict[str, Any]:
    """Helper: propose, backfill legacy 5-row shape, approve, advance ``target_seq`` times.

    Phase G G2: backfills steps 2..5 directly so the existing
    ``post_advance`` linear-fall-through handler can walk past
    seq=1 (post-G2 propose only inserts ``steps[0]``). Mirrors the
    pre-G2 in-flight activity that the migration explicitly
    preserves.
    """
    activity = _propose_with_legacy_5_rows(client, parent_headers, db_path)
    aid = activity["id"]
    approve = client.post(
        f"/api/activities/{aid}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert approve.status_code == 200, approve.text
    state = approve.json()
    for _ in range(target_seq):
        adv = client.post(
            f"/api/activities/{aid}/advance",
            headers={**parent_headers, "If-Match-Version": str(state["version"])},
        )
        assert adv.status_code == 200, adv.text
        state = adv.json()
    return state


def _current_seq(activity: dict[str, Any]) -> int:
    for step in activity["steps"]:
        if step["current"]:
            return int(step["seq"])
    raise AssertionError("no current step")


def test_step_back_decrements_current_and_increments_version(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    state = _approve_and_advance_to_seq(
        client, parent_headers, target_seq=3, db_path=db_path
    )
    assert state["state"] == "running"
    assert _current_seq(state) == 3
    pre_version = state["version"]

    response = client.post(
        f"/api/activities/{state['id']}/step-back",
        headers={**parent_headers, "If-Match-Version": str(pre_version)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "running"
    assert body["version"] == pre_version + 1
    assert _current_seq(body) == 2


def test_step_back_at_first_step_rejects(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    state = _approve_and_advance_to_seq(
        client, parent_headers, target_seq=1, db_path=db_path
    )
    assert _current_seq(state) == 1
    response = client.post(
        f"/api/activities/{state['id']}/step-back",
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_prior_step"


def test_step_back_from_approved_rejects(
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
    response = client.post(
        f"/api/activities/{activity['id']}/step-back",
        headers={
            **parent_headers,
            "If-Match-Version": str(approve.json()["version"]),
        },
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_transition"
    assert detail["current_state"] == "approved"


def test_step_back_from_completed_rejects(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    state = _approve_and_advance_to_seq(
        client, parent_headers, target_seq=5, db_path=db_path
    )
    # Phase L two-phase terminal advance: Phase 1 may insert a reward
    # step and keep state=running; Phase 2 flips to completed. Drive
    # both to land on state=completed before exercising step-back.
    final = client.post(
        f"/api/activities/{state['id']}/advance",
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert final.status_code == 200
    final_state = final.json()
    if final_state["state"] == "running":
        final = client.post(
            f"/api/activities/{state['id']}/advance",
            headers={**parent_headers, "If-Match-Version": str(final_state["version"])},
        )
        assert final.status_code == 200, final.text
        final_state = final.json()
    assert final_state["state"] == "completed"
    response = client.post(
        f"/api/activities/{state['id']}/step-back",
        headers={
            **parent_headers,
            "If-Match-Version": str(final_state["version"]),
        },
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_transition"
    assert detail["current_state"] == "completed"


def test_step_back_from_paused_succeeds(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """paused state allows step-back; activity stays paused, seq decrements."""
    state = _approve_and_advance_to_seq(
        client, parent_headers, target_seq=2, db_path=db_path
    )
    assert state["state"] == "running"
    assert _current_seq(state) == 2

    pause = client.post(
        f"/api/activities/{state['id']}/pause",
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert pause.status_code == 200, pause.text
    assert pause.json()["state"] == "paused"
    pause_version = pause.json()["version"]

    response = client.post(
        f"/api/activities/{state['id']}/step-back",
        headers={**parent_headers, "If-Match-Version": str(pause_version)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "paused"
    assert body["version"] >= pause_version + 1
    assert _current_seq(body) == 1
    seq_to_current = {step["seq"]: step["current"] for step in body["steps"]}
    assert seq_to_current[1] is True
    assert seq_to_current[2] is False


def test_step_back_version_conflict_rejects(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    state = _approve_and_advance_to_seq(
        client, parent_headers, target_seq=3, db_path=db_path
    )
    stale_version = state["version"] - 1
    response = client.post(
        f"/api/activities/{state['id']}/step-back",
        headers={**parent_headers, "If-Match-Version": str(stale_version)},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "version_conflict"
    assert detail["current_version"] == state["version"]
    assert detail["current_state"] == "running"


def test_step_back_publishes_activity_state_envelope(
    client: TestClient,
    parent_headers: dict[str, str],
    pubsub: PubSub,
    db_path: Path,
) -> None:
    state = _approve_and_advance_to_seq(
        client, parent_headers, target_seq=3, db_path=db_path
    )
    sub = pubsub.subscribe([Topic.activity_state])
    try:
        response = client.post(
            f"/api/activities/{state['id']}/step-back",
            headers={**parent_headers, "If-Match-Version": str(state["version"])},
        )
        assert response.status_code == 200
        envelope = sub.get_nowait()
        assert envelope.topic is Topic.activity_state
        assert envelope.payload["id"] == state["id"]
        # Payload mirrors the REST response so the kiosk re-renders the
        # rolled-back step from the steps[] array.
        steps = envelope.payload["steps"]
        current = next(s for s in steps if s["current"])
        assert current["seq"] == 2
    finally:
        sub.close()


@pytest.mark.parametrize(
    "missing_step",
    ["approve", "dismiss", "regenerate", "advance", "step-back", "end", "didnt-work"],
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
