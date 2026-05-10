"""End-to-end coverage of labeled_events writes from the activities API.

Verifies:
* propose writes a labeled_events row before returning
* dismiss writes parent_signal=-1
* end writes parent_signal=-0.5 with ended_at_step
* thumbs-up writes parent_signal=+1
* the recorder can't break the parent flow (a corrupt context still
  returns the activity)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.ai.labeled_events import (
    PARENT_SIGNAL_DISMISS,
    PARENT_SIGNAL_END_EARLY,
    PARENT_SIGNAL_THUMBS_UP,
)
from toybox.db.connection import connect

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


def _read_labeled(db_path: Path, activity_id: str) -> sqlite3.Row | None:
    conn = connect(db_path)
    try:
        row: sqlite3.Row | None = conn.execute(
            "SELECT * FROM labeled_events WHERE activity_id = ?",
            (activity_id,),
        ).fetchone()
        return row
    finally:
        conn.close()


def test_propose_writes_labeled_event_row(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    activity = _propose(client, parent_headers)
    row = _read_labeled(db_path, activity["id"])
    assert row is not None, "propose must create a labeled_events row"
    assert row["generator_path"] == "offline"
    assert row["parent_signal"] is None
    assert row["judge_scores_json"] is None
    # ChatML JSON shape
    import json as _json

    chatml = _json.loads(row["inputs_chatml_json"])
    assert chatml[0]["role"] == "system"
    assert chatml[1]["role"] == "user"


def test_dismiss_writes_signal_minus_one(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    activity = _propose(client, parent_headers)
    resp = client.post(
        f"/api/activities/{activity['id']}/dismiss",
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert resp.status_code == 200, resp.text
    row = _read_labeled(db_path, activity["id"])
    assert row is not None
    assert row["parent_signal"] == PARENT_SIGNAL_DISMISS
    assert row["parent_signal_set_at"] is not None


def test_end_writes_signal_minus_half_and_step(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    # propose → approve → advance → end
    activity = _propose(client, parent_headers)
    approve = client.post(
        f"/api/activities/{activity['id']}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert approve.status_code == 200, approve.text
    state = approve.json()
    advance = client.post(
        f"/api/activities/{activity['id']}/advance",
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert advance.status_code == 200, advance.text
    state = advance.json()

    end = client.post(
        f"/api/activities/{activity['id']}/end",
        headers={**parent_headers, "If-Match-Version": str(state["version"])},
    )
    assert end.status_code == 200, end.text
    row = _read_labeled(db_path, activity["id"])
    assert row is not None
    assert row["parent_signal"] == PARENT_SIGNAL_END_EARLY
    # ended_at_step records the seq of the step that was current when end was called
    assert row["ended_at_step"] is not None
    assert int(row["ended_at_step"]) >= 1


def test_thumbs_up_writes_signal_plus_one(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    activity = _propose(client, parent_headers)
    resp = client.post(
        f"/api/activities/{activity['id']}/thumbs-up",
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Activity unchanged: no version bump, same state
    assert body["state"] == activity["state"]
    assert body["version"] == activity["version"]
    row = _read_labeled(db_path, activity["id"])
    assert row is not None
    assert row["parent_signal"] == PARENT_SIGNAL_THUMBS_UP


def test_thumbs_up_idempotent(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    activity = _propose(client, parent_headers)
    for _ in range(3):
        resp = client.post(
            f"/api/activities/{activity['id']}/thumbs-up",
            headers=parent_headers,
        )
        assert resp.status_code == 200
    row = _read_labeled(db_path, activity["id"])
    assert row is not None
    assert row["parent_signal"] == PARENT_SIGNAL_THUMBS_UP


def test_thumbs_up_404_for_missing_activity(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    resp = client.post(
        "/api/activities/does-not-exist/thumbs-up",
        headers=parent_headers,
    )
    assert resp.status_code == 404


def test_signal_writes_dont_block_when_no_labeled_row_exists(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Drop the labeled_events row; dismiss must still succeed (best-effort)."""
    activity = _propose(client, parent_headers)
    # Wipe the labeled_events row to simulate a pre-step-15 activity
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "DELETE FROM labeled_events WHERE activity_id = ?",
                (activity["id"],),
            )
    finally:
        conn.close()
    resp = client.post(
        f"/api/activities/{activity['id']}/dismiss",
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert resp.status_code == 200, resp.text
    # And no labeled_events row exists either (signal is best-effort)
    row = _read_labeled(db_path, activity["id"])
    assert row is None


def test_propose_schedules_judge_when_in_sample(
    app: Any,
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1: propose must wire the judge sampler end-to-end through HTTP.

    With ``TOYBOX_EVAL_JUDGE_RATE=1`` every row is in-sample. We
    override the ``get_judge_call`` FastAPI dependency with a
    recording stub; after POST /api/activities/propose returns 201 the
    stub MUST have been invoked at least once with the new row's id.

    This test is the load-bearing proof that the eval scaffold's
    downstream is reachable from production code paths — without this
    wiring ``judge_scores_json`` would always be NULL and the Phase E
    SFT export query would return zero rows.
    """
    import threading
    import time

    from toybox.api.activities import get_judge_call

    monkeypatch.setenv("TOYBOX_EVAL_JUDGE_RATE", "1")

    judge_calls: list[dict[str, Any]] = []
    judge_done = threading.Event()

    async def _judge_stub(
        *, activity: Any, ctx: Any, row_id: int
    ) -> None:
        # Record the call + signal the test thread we fired. Using a
        # ``threading.Event`` (not ``asyncio.Event``) so the test thread
        # — which sits outside the FastAPI/anyio loop the task runs on
        # — can wait for completion without hopping between loops.
        judge_calls.append(
            {"activity_id": activity.id, "row_id": row_id, "ctx_intent": ctx.intent}
        )
        judge_done.set()

    # Inject the stub via the FastAPI dependency override.
    app.dependency_overrides[get_judge_call] = lambda: _judge_stub

    response = client.post(
        "/api/activities/propose",
        json=PROPOSE_BODY,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    activity_body = response.json()

    # The judge stub is a detached Task on the FastAPI portal's loop.
    # Issue a no-op GET to give the loop a chance to drain pending
    # tasks, then wait for the threading.Event with a generous timeout.
    _ = client.get(f"/api/activities/{activity_body['id']}", headers=parent_headers)
    fired = judge_done.wait(timeout=5.0)
    if not fired:
        # Last-ditch: poll for a moment in case the loop scheduled but
        # didn't yet run our coroutine (no awaits, so should be one tick).
        for _ in range(10):
            if judge_done.is_set():
                break
            time.sleep(0.05)
    assert judge_done.is_set(), (
        "expected the judge stub to fire — wiring from _do_propose to "
        "schedule_judge_sample is the load-bearing F1 fix"
    )

    matching = [c for c in judge_calls if c["activity_id"] == activity_body["id"]]
    assert matching, f"no judge call for our activity in {judge_calls}"
    call = matching[0]
    assert call["row_id"] > 0
    assert call["ctx_intent"] == PROPOSE_BODY["intent"]

    # And the labeled_events row exists
    row = _read_labeled(db_path, activity_body["id"])
    assert row is not None


def test_propose_skips_judge_when_judge_call_is_none(
    app: Any,
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1 (negative): ``get_judge_call`` returning None disables sampling.

    Even with rate=1 (every row in-sample), a None judge_call must
    short-circuit cleanly — no exception, no scheduled task, just a
    plain successful propose. Mirrors the production "no OAuth token"
    case.
    """
    from toybox.api.activities import get_judge_call

    monkeypatch.setenv("TOYBOX_EVAL_JUDGE_RATE", "1")
    app.dependency_overrides[get_judge_call] = lambda: None

    response = client.post(
        "/api/activities/propose",
        json=PROPOSE_BODY,
        headers=parent_headers,
    )
    assert response.status_code == 201
    row = _read_labeled(db_path, response.json()["id"])
    assert row is not None
    # judge_scores stays NULL — no scheduling fired
    assert row["judge_scores_json"] is None


def test_propose_succeeds_when_recorder_raises(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M1: a recorder failure must NOT break the kid-facing path.

    Patch :func:`record_generation` to raise; POST /api/activities/propose
    must still return 201 with a valid Activity body, and the activity
    row itself must still be persisted (the recorder is observability —
    a failure in it MUST NOT corrupt the lifecycle).
    """
    def _boom(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("simulated labeled_events failure")

    # Patch where the propose handler imports record_generation from.
    monkeypatch.setattr("toybox.api.activities.record_generation", _boom)

    response = client.post(
        "/api/activities/propose",
        json=PROPOSE_BODY,
        headers=parent_headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"]
    assert body["state"] == "proposed"
    # Phase G G2: lazy insertion → propose response carries only steps[0].
    assert len(body["steps"]) == 1

    # Activity itself was persisted; only the labeled_events row is missing.
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, state FROM activities WHERE id = ?",
            (body["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["state"] == "proposed"
    # No labeled_events row, because the recorder raised
    assert _read_labeled(db_path, body["id"]) is None
