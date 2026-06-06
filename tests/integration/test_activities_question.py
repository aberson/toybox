"""Phase R Step R3 — Q&A gating integration tests.

Covers:
* (a) advance returns 409 ``question_pending`` when the current step has a
  non-NULL ``question`` and NULL ``question_approved``.
* (b) approve-question sets ``question_approved`` and bumps ``activities.version``.
* (c) advance succeeds after the question is approved or skipped.
* (d) steps WITHOUT a ``question`` column advance normally (no regression).
* (e) approve-question broadcasts a WS ``activity.state`` envelope so the
  child kiosk unhides the Next button.

Each test stages a running activity by propose → approve → advance (first
advance flips approved → running on seq=1). It then injects a ``question``
value directly into the DB row to exercise the Q&A gate without needing a
new template fixture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
from toybox.ws.topics import Topic

# ---------------------------------------------------------------------------
# Helpers — propose / approve / advance.
# ---------------------------------------------------------------------------


def _propose(
    client: TestClient,
    parent_headers: dict[str, str],
    *,
    seed: int = 42,
) -> dict[str, Any]:
    resp = client.post(
        "/api/activities/propose",
        json={"intent": "request_play", "slot": "unicorns", "hour": 12, "seed": seed},
        headers=parent_headers,
    )
    assert resp.status_code == 201, resp.text
    return cast("dict[str, Any]", resp.json())


def _approve(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    version: int,
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
    expected_status: int = 200,
) -> dict[str, Any]:
    resp = client.post(
        f"/api/activities/{activity_id}/advance",
        headers={**parent_headers, "If-Match-Version": str(version)},
    )
    assert resp.status_code == expected_status, resp.text
    return cast("dict[str, Any]", resp.json())


def _approve_question(
    client: TestClient,
    parent_headers: dict[str, str],
    activity_id: str,
    version: int,
    result: str,
    *,
    expected_status: int = 200,
) -> dict[str, Any]:
    resp = client.post(
        f"/api/activities/{activity_id}/approve-question",
        json={"result": result, "version": version},
        headers=parent_headers,
    )
    assert resp.status_code == expected_status, resp.text
    return cast("dict[str, Any]", resp.json())


def _inject_question(db_path: Path, activity_id: str, question: str) -> None:
    """Set ``question`` on the current (current=1) step of ``activity_id``."""
    conn = connect(db_path, check_same_thread=False)
    try:
        with conn:
            conn.execute(
                "UPDATE activity_steps SET question = ? "
                "WHERE activity_id = ? AND current = 1",
                (question, activity_id),
            )
    finally:
        conn.close()


def _stage_running(
    client: TestClient,
    parent_headers: dict[str, str],
    *,
    seed: int = 42,
) -> dict[str, Any]:
    """Propose → approve → advance (first advance flips to running).

    Returns the activity response after the first advance (state=running).
    """
    body = _propose(client, parent_headers, seed=seed)
    state = _approve(client, parent_headers, body["id"], body["version"])
    state = _advance(client, parent_headers, body["id"], state["version"])
    assert state["state"] == "running", f"expected running, got {state['state']}"
    return state


# ---------------------------------------------------------------------------
# (a) advance returns 409 question_pending when question IS NOT NULL and
#     question_approved IS NULL.
# ---------------------------------------------------------------------------


def test_advance_blocks_on_pending_question(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Advance must 409 ``question_pending`` when the current step has a
    question that the parent has not yet resolved.
    """
    state = _stage_running(client, parent_headers)
    activity_id = state["id"]

    _inject_question(db_path, activity_id, "What is your favourite colour?")

    resp_body = _advance(
        client,
        parent_headers,
        activity_id,
        state["version"],
        expected_status=409,
    )
    # detail is now a dict with code="question_pending" — consistent with
    # every other 409 in the file.
    assert resp_body.get("detail", {}).get("code") == "question_pending", (
        f"expected detail.code='question_pending', got {resp_body!r}"
    )


# ---------------------------------------------------------------------------
# (b) approve-question sets question_approved and bumps version.
# ---------------------------------------------------------------------------


def test_approve_question_sets_approved_flag_and_bumps_version(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """POST approve-question returns {version: N+1} and persists
    question_approved=1 (approved) in the DB.
    """
    state = _stage_running(client, parent_headers)
    activity_id = state["id"]
    version_before = state["version"]

    _inject_question(db_path, activity_id, "Can you name an element?")

    result = _approve_question(
        client, parent_headers, activity_id, version_before, "approved"
    )
    assert result["version"] == version_before + 1, (
        f"version should bump by 1: before={version_before} response={result['version']}"
    )

    # Verify the DB row.
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT question_approved FROM activity_steps "
            "WHERE activity_id = ? AND current = 1",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "no current step found"
    # question_approved=1 means "approved", 2 would mean "skipped".
    assert int(row["question_approved"]) == 1, (
        f"expected question_approved=1 (approved), got {row['question_approved']!r}"
    )


def test_approve_question_skip_sets_skipped_flag(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """POST approve-question with result=skipped persists question_approved=2."""
    state = _stage_running(client, parent_headers, seed=43)
    activity_id = state["id"]

    _inject_question(db_path, activity_id, "What sound does a cat make?")

    result = _approve_question(
        client, parent_headers, activity_id, state["version"], "skipped"
    )
    assert result["version"] == state["version"] + 1

    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT question_approved FROM activity_steps "
            "WHERE activity_id = ? AND current = 1",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert int(row["question_approved"]) == 2, (
        f"expected question_approved=2 (skipped), got {row['question_approved']!r}"
    )


# ---------------------------------------------------------------------------
# (c) advance succeeds after the question is approved.
# ---------------------------------------------------------------------------


def test_advance_succeeds_after_question_approved(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """After approve-question, POST /advance must succeed (200)."""
    state = _stage_running(client, parent_headers, seed=44)
    activity_id = state["id"]

    _inject_question(db_path, activity_id, "What is your favourite animal?")

    # Advance must be blocked first.
    _advance(
        client,
        parent_headers,
        activity_id,
        state["version"],
        expected_status=409,
    )

    # Approve the question.
    result = _approve_question(
        client, parent_headers, activity_id, state["version"], "approved"
    )
    new_version = result["version"]

    # Now advance must succeed.
    state = _advance(client, parent_headers, activity_id, new_version)
    assert state["state"] in {"running", "completed"}, (
        f"expected running or completed after advance, got {state['state']}"
    )
    # The current step's question_pending must now be False (or the step
    # has advanced and there's no pending question on the new step).
    current_steps = [s for s in state["steps"] if s.get("current")]
    for s in current_steps:
        assert s.get("question_pending") is not True, (
            f"current step should not have question_pending=True after approval: {s!r}"
        )


def test_advance_succeeds_after_question_skipped(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """After approve-question with result=skipped, POST /advance must succeed."""
    state = _stage_running(client, parent_headers, seed=45)
    activity_id = state["id"]

    _inject_question(db_path, activity_id, "Name a colour?")

    _approve_question(
        client, parent_headers, activity_id, state["version"], "skipped"
    )

    # Verify skip also unblocks advance.
    conn = connect(db_path, check_same_thread=False)
    try:
        new_ver = conn.execute(
            "SELECT version FROM activities WHERE id = ?", (activity_id,)
        ).fetchone()["version"]
    finally:
        conn.close()

    state = _advance(client, parent_headers, activity_id, new_ver)
    assert state["state"] in {"running", "completed"}


# ---------------------------------------------------------------------------
# (d) steps WITHOUT a question advance normally (regression guard).
# ---------------------------------------------------------------------------


def test_advance_no_question_proceeds_normally(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Activities without a question on the current step must advance
    without being blocked (regression guard for the Q&A gate logic).
    """
    state = _stage_running(client, parent_headers, seed=99)
    activity_id = state["id"]

    # Verify no question is set on the current step.
    conn = connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT question FROM activity_steps WHERE activity_id = ? AND current = 1",
            (activity_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["question"] is None, (
        f"expected no question on the step, got {row['question']!r}"
    )

    # Advance should succeed without approve-question.
    state = _advance(client, parent_headers, activity_id, state["version"])
    assert state["state"] in {"running", "completed"}, (
        f"no-question advance should succeed; got state={state['state']}"
    )


# ---------------------------------------------------------------------------
# (e) approve-question broadcasts a WS activity.state envelope.
# ---------------------------------------------------------------------------


def test_approve_question_broadcasts_ws_envelope(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    pubsub: PubSub,
) -> None:
    """POST approve-question must emit a ``activity.state`` WS envelope so
    the child kiosk receives the updated question_pending=False payload and
    unhides the Next button.
    """
    state = _stage_running(client, parent_headers, seed=77)
    activity_id = state["id"]

    _inject_question(db_path, activity_id, "What colour is the sky?")

    sub = pubsub.subscribe([Topic.activity_state])
    try:
        result = _approve_question(
            client, parent_headers, activity_id, state["version"], "approved"
        )
        assert result["version"] == state["version"] + 1

        envelope = sub.get_nowait()
        assert envelope.topic is Topic.activity_state
        assert envelope.payload["id"] == activity_id
        # The payload must carry the new version number.
        assert envelope.payload["version"] == result["version"], (
            f"WS envelope version mismatch: payload={envelope.payload['version']} "
            f"response={result['version']}"
        )
        # The current step's question_pending must be False in the WS payload
        # so the child kiosk unhides the Next button.
        current_ws_steps = [s for s in envelope.payload.get("steps", []) if s.get("current")]
        for s in current_ws_steps:
            assert s.get("question_pending") is not True, (
                f"WS envelope current step has question_pending=True after approval: {s!r}"
            )
    finally:
        sub.close()


# ---------------------------------------------------------------------------
# (f) approve-question 409 paths: version_conflict and invalid_transition.
# ---------------------------------------------------------------------------


def test_approve_question_stale_version_returns_409_version_conflict(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """POST approve-question with a stale version returns 409 version_conflict."""
    state = _stage_running(client, parent_headers, seed=50)
    activity_id = state["id"]

    _inject_question(db_path, activity_id, "What is your name?")

    # Send version=1 — guaranteed stale: _stage_running does propose →
    # approve → advance so the real version is at least 3.
    resp = client.post(
        f"/api/activities/{activity_id}/approve-question",
        json={"result": "approved", "version": 1},
        headers=parent_headers,
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "version_conflict"


def test_approve_question_invalid_state_returns_409_invalid_transition(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """POST approve-question on a PROPOSED (not yet running) activity returns
    409 invalid_transition.
    """
    # Propose but do NOT approve → state stays "proposed".
    body = _propose(client, parent_headers, seed=51)
    activity_id = body["id"]
    version = body["version"]

    # Inject a question into the first step so the endpoint can't early-exit
    # on a missing question (state guard fires first, but this makes the
    # intent clearer).
    _inject_question(db_path, activity_id, "What colour is the sky?")

    resp = client.post(
        f"/api/activities/{activity_id}/approve-question",
        json={"result": "approved", "version": version},
        headers=parent_headers,
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "invalid_transition"
