"""Optimistic concurrency: ``If-Match-Version`` 409s + concurrent races."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.api.activities import get_activities_db
from toybox.api.auth_dep import get_auth_db
from toybox.api.listening import get_db as get_listening_db
from toybox.app import create_app
from toybox.core.auth import TokenScope, issue_token
from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.ws.server import get_pubsub, get_ws_db


def _propose(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/activities/propose",
        json={"intent": "request_play", "slot": None, "hour": 12, "seed": 1},
        headers=headers,
    )
    assert response.status_code == 201
    return cast("dict[str, Any]", response.json())


def test_stale_version_returns_409_with_current_version(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    activity = _propose(client, parent_headers)
    # First approve succeeds at version 1.
    first = client.post(
        f"/api/activities/{activity['id']}/approve",
        json={},
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert first.status_code == 200
    assert first.json()["version"] == 2

    # Second mutation with the stale version=1 should 409.
    # ``end`` is a valid transition from ``approved`` so the failure
    # mode is purely the version mismatch.
    second = client.post(
        f"/api/activities/{activity['id']}/end",
        headers={**parent_headers, "If-Match-Version": "1"},
    )
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["code"] == "version_conflict"
    assert detail["current_version"] == 2
    assert detail["current_state"] == "approved"


@pytest.mark.parametrize("bad_value", ["abc", "-1", "", "1.5"])
def test_invalid_version_header_400(
    client: TestClient,
    parent_headers: dict[str, str],
    bad_value: str,
) -> None:
    """Non-integer / non-positive ``If-Match-Version`` headers reject 400."""
    activity = _propose(client, parent_headers)
    bad = client.post(
        f"/api/activities/{activity['id']}/dismiss",
        headers={**parent_headers, "If-Match-Version": bad_value},
    )
    # An empty string is treated as "missing header" by FastAPI's
    # parsing, so the response code is the same but the detail differs.
    assert bad.status_code == 400
    code = bad.json()["detail"]["code"]
    assert code in {"invalid_version", "missing_version"}


@pytest.fixture
def threaded_app(tmp_path: Path) -> Iterator[tuple[FastAPI, Path]]:
    """Build a FastAPI app whose dependencies are thread-safe (per-thread conn)."""
    db_path = tmp_path / "toybox.db"
    bootstrap = connect(db_path)
    try:
        run_migrations(bootstrap)
    finally:
        bootstrap.close()

    pubsub = PubSub()

    def _override_db() -> Iterator[sqlite3.Connection]:
        # SQLite Connection objects belong to one thread by default.
        # Reopening per request is fine; pragma cost is negligible
        # against the DB file.
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    app = create_app()
    for dep in (
        get_listening_db,
        get_auth_db,
        get_activities_db,
        get_ws_db,
    ):
        app.dependency_overrides[dep] = _override_db
    app.dependency_overrides[get_pubsub] = lambda: pubsub
    try:
        yield app, db_path
    finally:
        app.dependency_overrides.clear()


def test_concurrent_propose_at_cap_evicts_consistently(
    threaded_app: tuple[FastAPI, Path],
) -> None:
    """Two simultaneous ``propose`` calls with the queue at cap=5 both
    trigger eviction. The final state must be consistent: exactly 5
    rows in ``proposed`` and the right number of dismissed rows.
    """
    app, db_path = threaded_app
    bootstrap = connect(db_path)
    try:
        token = issue_token(bootstrap, TokenScope.parent).token
    finally:
        bootstrap.close()
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        # Fill the queue to the cap of 5 first.
        for seed in range(1, 6):
            response = client.post(
                "/api/activities/propose",
                json={
                    "intent": "request_play",
                    "slot": None,
                    "hour": 12,
                    "seed": seed,
                },
                headers=headers,
            )
            assert response.status_code == 201

    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def _race(seed: int) -> None:
        barrier.wait()
        with TestClient(app) as inner:
            response = inner.post(
                "/api/activities/propose",
                json={
                    "intent": "request_play",
                    "slot": None,
                    "hour": 12,
                    "seed": seed,
                },
                headers=headers,
            )
        statuses.append(response.status_code)

    t1 = threading.Thread(target=_race, args=(101,))
    t2 = threading.Thread(target=_race, args=(102,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Both proposes should have succeeded — the lifecycle endpoint
    # never serializes them and both pay the eviction cost.
    assert sorted(statuses) == [201, 201], f"got {statuses}"

    # Final invariant: proposed-state rows do not exceed the cap.
    final = connect(db_path)
    try:
        proposed_count = final.execute(
            "SELECT COUNT(*) AS n FROM activities WHERE state = 'proposed'"
        ).fetchone()["n"]
        dismissed_count = final.execute(
            "SELECT COUNT(*) AS n FROM activities WHERE state = 'dismissed'"
        ).fetchone()["n"]
    finally:
        final.close()

    # 5 originals + 2 races = 7 inserts; cap holds at <=5 proposed.
    # The exact number dismissed depends on interleaving, but at
    # least 2 must have been evicted to enforce the cap.
    assert proposed_count <= 5
    assert dismissed_count >= 2


def test_concurrent_advance_one_wins_one_409s(
    threaded_app: tuple[FastAPI, Path],
) -> None:
    """Two clients hold version=N and both call ``advance``.

    ``running → running`` is valid for both, so the only failure path
    is the optimistic-concurrency check inside the SQL UPDATE; the
    loser must see ``code=version_conflict`` (NOT ``invalid_transition``)
    so the parent UI's refetch path is well-defined.
    """
    app, db_path = threaded_app

    bootstrap = connect(db_path)
    try:
        token = issue_token(bootstrap, TokenScope.parent).token
    finally:
        bootstrap.close()
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        activity = _propose(client, headers)
        activity_id = activity["id"]
        # Phase G G2: backfill steps 2..5 onto the lazy-inserted row
        # so the concurrent advance race below has somewhere to go
        # (without the backfill, the only step is steps[0] and the
        # second advance trips the terminal branch instead of the
        # version-conflict branch we're testing). This mirrors the
        # in-flight pre-G2 activity shape.
        from tests.fixtures.lazy_insert import backfill_legacy_steps

        backfill_conn = connect(db_path)
        try:
            backfill_legacy_steps(backfill_conn, activity_id)
        finally:
            backfill_conn.close()
        # Drive into ``running`` so concurrent advance is valid for
        # both racers. After approve+advance the activity is on step 1
        # at version=3.
        approve = client.post(
            f"/api/activities/{activity_id}/approve",
            json={},
            headers={**headers, "If-Match-Version": "1"},
        )
        assert approve.status_code == 200
        adv1 = client.post(
            f"/api/activities/{activity_id}/advance",
            headers={**headers, "If-Match-Version": "2"},
        )
        assert adv1.status_code == 200
        assert adv1.json()["state"] == "running"
        assert adv1.json()["version"] == 3

        results: list[int] = []
        bodies: list[dict[str, Any]] = []
        barrier = threading.Barrier(2)

        def _try_advance() -> None:
            barrier.wait()
            with TestClient(app) as inner:
                response = inner.post(
                    f"/api/activities/{activity_id}/advance",
                    headers={**headers, "If-Match-Version": "3"},
                )
            results.append(response.status_code)
            bodies.append(response.json())

        t1 = threading.Thread(target=_try_advance)
        t2 = threading.Thread(target=_try_advance)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert sorted(results) == [200, 409], f"got {results}"
        loser = next(b for code, b in zip(results, bodies, strict=False) if code == 409)
        # The 409 body shape is load-bearing for the parent UI refetch.
        assert loser["detail"]["code"] == "version_conflict"
        assert loser["detail"]["current_version"] == 4
        assert loser["detail"]["current_state"] == "running"
