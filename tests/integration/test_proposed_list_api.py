"""Integration coverage for ``GET /api/activities/proposed``.

The parent dashboard's play-queue UI calls this on mount to paint the
scrolling card stack (and, with ``include_active=true``, the currently-
playing card) before subscribing to the ``activity.state`` ws topic for
deltas. The endpoint is parent-scope only; child tokens 403.

Tests insert activity rows directly via SQL (bypassing the propose
generator) so each fixture row carries a known ``created_at`` and the
ordering / limit / active-row contracts can be asserted precisely.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from toybox.core.auth import TokenScope, issue_token
from toybox.db.connection import connect

_TEST_SESSION_ID = "test-list-session"


@pytest.fixture(autouse=True)
def _seed_session(db_path: Path) -> Iterator[None]:
    """Seed a sessions row so activity inserts have a valid FK target.

    The list endpoint doesn't require the production session id (it
    queries by ``state``, not ``session_id``), but the schema's FK on
    ``activities.session_id`` does require a real session row to exist.
    """
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (_TEST_SESSION_ID, "2026-01-01T00:00:00Z"),
            )
        yield
    finally:
        conn.close()


def _insert_activity(
    db_path: Path,
    *,
    state: str,
    created_at: str,
    title: str | None = None,
) -> str:
    """Insert one activity row directly; return its id.

    Title goes into the ``summary`` JSON envelope (the column is
    overloaded — see ``_row_to_response`` in
    :mod:`toybox.api.activities`) so the wire shape mirrors the
    propose path's output.
    """
    activity_id = str(uuid.uuid4())
    summary_blob = json.dumps({"title": title or "test", "metadata": {}}, sort_keys=True)
    conn = connect(db_path, check_same_thread=False)
    try:
        with conn:
            conn.execute(
                "INSERT INTO activities "
                "(id, session_id, state, version, summary, persona_id, "
                " child_ids, room_ids, toy_ids, intent_source, created_at, "
                " started_at, ended_at) "
                "VALUES (?, ?, ?, 1, ?, NULL, NULL, NULL, NULL, "
                "'list-test', ?, NULL, NULL)",
                (activity_id, _TEST_SESSION_ID, state, summary_blob, created_at),
            )
    finally:
        conn.close()
    return activity_id


def test_empty_db_returns_empty_items(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """No proposed rows → ``{"items": []}`` (no ``active`` key)."""
    response = client.get(
        "/api/activities/proposed",
        headers=parent_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    # Without ``include_active`` the response model still has ``active``
    # defaulting to ``None`` because pydantic emits all fields by
    # default. That's fine — the parent UI ignores it when it didn't
    # ask for it. The contract is just that ``items`` is correct.
    assert body.get("active") is None


def test_returns_proposed_rows_newest_first(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Three proposed rows → 3 items, ``created_at DESC`` order.

    The earliest-inserted row must appear LAST in the response so the
    parent UI's scrolling queue (newest on top) doesn't need a
    client-side sort.
    """
    older = _insert_activity(
        db_path, state="proposed", created_at="2026-05-10T00:00:00Z"
    )
    middle = _insert_activity(
        db_path, state="proposed", created_at="2026-05-10T01:00:00Z"
    )
    newest = _insert_activity(
        db_path, state="proposed", created_at="2026-05-10T02:00:00Z"
    )

    response = client.get(
        "/api/activities/proposed",
        headers=parent_headers,
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["id"] for item in items] == [newest, middle, older]


def test_limit_caps_at_five(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Seven proposed rows → only the five newest are returned."""
    inserted_ids: list[str] = []
    # Use distinct second-precision timestamps so the ordering tiebreak
    # is unambiguous; the endpoint's secondary sort on id is only a
    # safety net for same-second inserts.
    for hour in range(7):
        inserted_ids.append(
            _insert_activity(
                db_path,
                state="proposed",
                created_at=f"2026-05-10T0{hour}:00:00Z",
            )
        )

    response = client.get(
        "/api/activities/proposed",
        headers=parent_headers,
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 5
    # The five newest are inserted_ids[2..6] in DESC order.
    expected_ids = list(reversed(inserted_ids[-5:]))
    assert [item["id"] for item in items] == expected_ids


def test_include_active_with_running_row(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """``include_active=true`` surfaces the most-recent non-terminal row.

    Three proposed rows + one running row → response carries the 3
    proposed items in ``items`` and the running row as ``active``. The
    running row is NOT in ``items`` (only ``proposed`` rows are).
    """
    for hour in range(3):
        _insert_activity(
            db_path,
            state="proposed",
            created_at=f"2026-05-10T0{hour}:00:00Z",
        )
    running_id = _insert_activity(
        db_path,
        state="running",
        created_at="2026-05-10T05:00:00Z",
    )

    response = client.get(
        "/api/activities/proposed?include_active=true",
        headers=parent_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 3
    for item in body["items"]:
        assert item["state"] == "proposed"
    assert body["active"] is not None
    assert body["active"]["id"] == running_id
    assert body["active"]["state"] == "running"


def test_include_active_picks_most_recent_across_active_states(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """``approved`` / ``running`` / ``paused`` / ``completed`` all qualify.

    Inserts one of each in ascending ``created_at``; the most recent
    (``completed``) must win. Pins the ``_ACTIVE_STATES`` filter
    contract — a regression that dropped one of these states would
    silently skip the row.
    """
    _insert_activity(db_path, state="approved", created_at="2026-05-10T01:00:00Z")
    _insert_activity(db_path, state="running", created_at="2026-05-10T02:00:00Z")
    _insert_activity(db_path, state="paused", created_at="2026-05-10T03:00:00Z")
    completed_id = _insert_activity(
        db_path, state="completed", created_at="2026-05-10T04:00:00Z"
    )
    # A terminal row at the very latest timestamp must NOT win — it's
    # filtered out by the active-state set.
    _insert_activity(db_path, state="ended", created_at="2026-05-10T05:00:00Z")

    response = client.get(
        "/api/activities/proposed?include_active=true",
        headers=parent_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["active"] is not None
    assert body["active"]["id"] == completed_id


def test_include_active_returns_null_when_none(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """``include_active=true`` with only proposed/terminal rows → ``active=null``."""
    _insert_activity(
        db_path, state="proposed", created_at="2026-05-10T00:00:00Z"
    )
    _insert_activity(
        db_path, state="dismissed", created_at="2026-05-10T01:00:00Z"
    )

    response = client.get(
        "/api/activities/proposed?include_active=true",
        headers=parent_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["active"] is None


def test_child_token_forbidden(
    client: TestClient,
    db_path: Path,
) -> None:
    """Child-scope token gets 403 — the list endpoint is parent-only."""
    # Insert a child profile so the token is issuable, then mint a
    # child-scope token directly against the test DB.
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children (id, display_name) VALUES (?, ?)",
                ("child-1", "Test Child"),
            )
        child_token = issue_token(
            conn, TokenScope.child, child_session_label="child-1"
        ).token
    finally:
        conn.close()
    headers = {"Authorization": f"Bearer {child_token}"}

    response = client.get("/api/activities/proposed", headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "auth_scope_forbidden"


def test_missing_token_unauthorized(client: TestClient) -> None:
    """No token → 401."""
    response = client.get("/api/activities/proposed")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "auth_required"
