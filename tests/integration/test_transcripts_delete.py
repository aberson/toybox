"""HTTP coverage for the Step 22 transcript-management DELETE surface.

Two endpoints under test:

* ``DELETE /api/transcripts/{id}`` — single delete, parent-token only.
* ``DELETE /api/transcripts`` — wipe all, parent-token + PIN re-confirm.

The wipe-all endpoint shares the process-wide PIN rate limiter with
``POST /api/auth/parent``; the conftest already injects a fresh
:class:`PinRateLimiter` per test via ``dependency_overrides`` so the
counter never bleeds across cases.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from toybox.core.pin import set_pin_hash
from toybox.db.connection import connect

GOOD_PIN = "1357"
WRONG_PIN = "9999"


# ---------------------------------------------------------------------
# Helpers — copy/pasted from test_transcripts_api.py (no shared fixture
# yet; cross-module helper extraction can wait for v1.5).
# ---------------------------------------------------------------------


def _seed_session(db_path: Path, session_id: str = "s1") -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (session_id, "2026-01-01T00:00:00Z"),
            )
    finally:
        conn.close()


def _insert_transcripts(
    db_path: Path,
    rows: Iterable[tuple[str, str, str, str, float, str]],
    *,
    session_id: str = "s1",
) -> None:
    """Bulk-insert ``(id, text, started_at, ended_at, confidence, language)``."""
    conn = connect(db_path)
    try:
        with conn:
            for row_id, text, started, ended, confidence, language in rows:
                conn.execute(
                    "INSERT INTO transcripts "
                    "(id, session_id, mic_id, started_at, ended_at, text, "
                    " confidence, language) "
                    "VALUES (?, ?, NULL, ?, ?, ?, ?, ?)",
                    (row_id, session_id, started, ended, text, confidence, language),
                )
    finally:
        conn.close()


def _set_pin(db_path: Path, pin: str = GOOD_PIN) -> None:
    conn = connect(db_path)
    try:
        set_pin_hash(conn, pin)
    finally:
        conn.close()


def _row_count(db_path: Path, table: str) -> int:
    conn = connect(db_path)
    try:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"])
    finally:
        conn.close()


# ---------------------------------------------------------------------
# DELETE /api/transcripts/{id}
# ---------------------------------------------------------------------


def test_delete_one_returns_ok_and_removes_row(
    client: TestClient,
    db_path: Path,
    parent_headers: dict[str, str],
) -> None:
    _seed_session(db_path)
    _insert_transcripts(
        db_path,
        [
            ("t-1", "first", "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", 0.7, "en"),
            ("t-2", "second", "2026-01-01T00:00:03Z", "2026-01-01T00:00:04Z", 0.8, "en"),
        ],
    )
    response = client.delete("/api/transcripts/t-1", headers=parent_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    # Survivor still listed; deleted row gone.
    assert _row_count(db_path, "transcripts") == 1
    listing = client.get("/api/transcripts").json()
    assert {r["id"] for r in listing["items"]} == {"t-2"}


def test_delete_one_unknown_id_returns_404(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    response = client.delete("/api/transcripts/not-a-real-id", headers=parent_headers)
    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["code"] == "transcript_not_found"
    assert body["detail"]["id"] == "not-a-real-id"


# Per-endpoint 401/403 cases for ``DELETE /api/transcripts/{id}`` are
# covered by the ``_PROTECTED_ENDPOINTS`` parametrize tables at the
# bottom of this module, so we don't repeat them here.


# ---------------------------------------------------------------------
# DELETE /api/transcripts (wipe all)
# ---------------------------------------------------------------------


def test_wipe_all_happy_path_returns_count_and_clears_rows(
    client: TestClient,
    db_path: Path,
    parent_headers: dict[str, str],
) -> None:
    _seed_session(db_path)
    _insert_transcripts(
        db_path,
        [
            ("t-1", "a", "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", 0.7, "en"),
            ("t-2", "b", "2026-01-01T00:00:03Z", "2026-01-01T00:00:04Z", 0.8, "en"),
            ("t-3", "c", "2026-01-01T00:00:05Z", "2026-01-01T00:00:06Z", 0.9, "en"),
        ],
    )
    _set_pin(db_path)
    response = client.request(
        "DELETE",
        "/api/transcripts",
        json={"pin": GOOD_PIN},
        headers=parent_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 3}
    assert _row_count(db_path, "transcripts") == 0


def test_wipe_all_does_not_cascade_to_other_tables(
    client: TestClient,
    db_path: Path,
    parent_headers: dict[str, str],
) -> None:
    """Wipe must NOT cascade to ``sessions``, ``activities``, or
    ``labeled_events``. ``transcripts`` has a FK to ``sessions`` (via
    ``ON DELETE RESTRICT``), and no other table holds a FK back into
    ``transcripts``, so the only risk would be a cross-table ON DELETE
    we accidentally introduced. Pin the invariant by counting before
    and after.
    """
    _seed_session(db_path)
    _insert_transcripts(
        db_path,
        [
            ("t-1", "a", "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", 0.7, "en"),
        ],
    )
    # Seed a labeled_events row directly so we can prove the wipe didn't
    # touch it. ``activity_id`` is logically (but not declaratively) a
    # foreign key — see migration 0003 — so we don't need a real
    # activities row.
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO labeled_events "
                "(activity_id, generated_at, generator_path, "
                " inputs_chatml_json, activity_json) "
                "VALUES (?, ?, ?, ?, ?)",
                ("act-1", "2026-01-01T00:00:00Z", "offline", "[]", "{}"),
            )
    finally:
        conn.close()

    _set_pin(db_path)

    sessions_before = _row_count(db_path, "sessions")
    labeled_before = _row_count(db_path, "labeled_events")
    activities_before = _row_count(db_path, "activities")

    response = client.request(
        "DELETE",
        "/api/transcripts",
        json={"pin": GOOD_PIN},
        headers=parent_headers,
    )
    assert response.status_code == 200

    assert _row_count(db_path, "sessions") == sessions_before
    assert _row_count(db_path, "labeled_events") == labeled_before
    assert _row_count(db_path, "activities") == activities_before
    assert _row_count(db_path, "transcripts") == 0


def test_wipe_all_wrong_pin_returns_401(
    client: TestClient,
    db_path: Path,
    parent_headers: dict[str, str],
) -> None:
    _seed_session(db_path)
    _insert_transcripts(
        db_path,
        [
            ("t-1", "a", "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", 0.7, "en"),
        ],
    )
    _set_pin(db_path)
    response = client.request(
        "DELETE",
        "/api/transcripts",
        json={"pin": WRONG_PIN},
        headers=parent_headers,
    )
    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail["code"] == "pin_invalid"
    assert detail["attempts_remaining"] == 4
    # Row not removed.
    assert _row_count(db_path, "transcripts") == 1


def test_wipe_all_locks_after_five_wrong_pins(
    client: TestClient,
    db_path: Path,
    parent_headers: dict[str, str],
) -> None:
    _seed_session(db_path)
    _set_pin(db_path)
    last: Any = None
    for _ in range(5):
        last = client.request(
            "DELETE",
            "/api/transcripts",
            json={"pin": WRONG_PIN},
            headers=parent_headers,
        )
    assert last is not None
    assert last.status_code == 423
    assert last.headers.get("Retry-After") is not None
    detail = last.json()["detail"]
    assert detail["code"] == "pin_locked"
    assert detail["seconds_until_unlock"] > 0
    # 6th attempt — even with the right PIN — still 423.
    response = client.request(
        "DELETE",
        "/api/transcripts",
        json={"pin": GOOD_PIN},
        headers=parent_headers,
    )
    assert response.status_code == 423


def test_wipe_all_lock_is_shared_with_login(
    client: TestClient,
    db_path: Path,
    parent_headers: dict[str, str],
) -> None:
    """5 wrong PINs across the auth + wipe surfaces locks both.

    Pins the cross-endpoint invariant: the rate limiter is process-wide
    and the wipe-all endpoint MUST honour a lock engaged by the login
    surface (and vice versa).
    """
    _seed_session(db_path)
    _set_pin(db_path)
    # 3 wrong via login, 2 wrong via wipe — total 5.
    for _ in range(3):
        client.post("/api/auth/parent", json={"pin": WRONG_PIN})
    for _ in range(2):
        client.request(
            "DELETE",
            "/api/transcripts",
            json={"pin": WRONG_PIN},
            headers=parent_headers,
        )
    # Now both surfaces should be locked.
    locked_login = client.post("/api/auth/parent", json={"pin": GOOD_PIN})
    locked_wipe = client.request(
        "DELETE",
        "/api/transcripts",
        json={"pin": GOOD_PIN},
        headers=parent_headers,
    )
    assert locked_login.status_code == 423
    assert locked_wipe.status_code == 423


# Per-endpoint 401/403 cases for ``DELETE /api/transcripts`` (wipe-all)
# are covered by the ``_PROTECTED_ENDPOINTS`` parametrize tables at the
# bottom of this module, so we don't repeat them here.


def test_wipe_all_no_body_returns_422(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Missing PIN body — Pydantic rejects before the limiter is touched."""
    response = client.request(
        "DELETE",
        "/api/transcripts",
        headers=parent_headers,
    )
    assert response.status_code == 422


def test_wipe_all_when_pin_not_set_returns_412(
    client: TestClient,
    db_path: Path,
    parent_headers: dict[str, str],
) -> None:
    """Defensive: hand-edited DB without a stored hash returns 412.

    In production the bind guard prevents this state from being
    reachable, but the endpoint still mirrors the auth shape so a stray
    request surfaces a recoverable error code rather than 500.
    """
    _seed_session(db_path)
    _insert_transcripts(
        db_path,
        [
            ("t-1", "a", "2026-01-01T00:00:01Z", "2026-01-01T00:00:02Z", 0.7, "en"),
        ],
    )
    response = client.request(
        "DELETE",
        "/api/transcripts",
        json={"pin": GOOD_PIN},
        headers=parent_headers,
    )
    assert response.status_code == 412
    assert response.json()["detail"]["code"] == "pin_not_set"
    # Row not removed.
    assert _row_count(db_path, "transcripts") == 1


def test_wipe_all_with_empty_table_returns_zero(
    client: TestClient,
    db_path: Path,
    parent_headers: dict[str, str],
) -> None:
    """Empty table + valid PIN → 200 with ``{deleted: 0}``.

    Pins the empty branch — ``rowcount`` of a no-op delete is 0, never
    -1 (we clamp negatives elsewhere; this exercises the natural-zero
    case so the clamp doesn't get tested in isolation).
    """
    _set_pin(db_path)
    response = client.request(
        "DELETE",
        "/api/transcripts",
        json={"pin": GOOD_PIN},
        headers=parent_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 0}


def test_wipe_attempts_remaining_decrements(
    client: TestClient,
    db_path: Path,
    parent_headers: dict[str, str],
) -> None:
    """Three wrong wipes — ``attempts_remaining`` ticks 4 → 3 → 2.

    Pins the counter surface across multiple wrong-PIN wipe attempts.
    The lock threshold is 5, so three failures stay under it; the
    response shape must match the login surface (same helper).
    """
    _set_pin(db_path)
    expected = [4, 3, 2]
    for remaining in expected:
        response = client.request(
            "DELETE",
            "/api/transcripts",
            json={"pin": WRONG_PIN},
            headers=parent_headers,
        )
        assert response.status_code == 401
        detail = response.json()["detail"]
        assert detail["code"] == "pin_invalid"
        assert detail["attempts_remaining"] == remaining


def test_wipe_failed_attempt_logs_count_not_pin(
    client: TestClient,
    db_path: Path,
    parent_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Spec invariant (mirrors Step 21): no log line carries the attempted PIN.

    The wipe handler routes through ``enforce_pin_check`` (shared with
    the login surface), but a separate test here pins the invariant
    against future drift if the wipe path ever grows its own log call.
    """
    _set_pin(db_path)
    secret_attempt = "9999"
    with caplog.at_level(logging.WARNING, logger="toybox.api.auth"):
        response = client.request(
            "DELETE",
            "/api/transcripts",
            json={"pin": secret_attempt},
            headers=parent_headers,
        )
    assert response.status_code == 401
    # Negative evidence: the attempted PIN never appears in any log.
    for record in caplog.records:
        msg = record.getMessage()
        assert secret_attempt not in msg, f"PIN leaked in log: {msg!r}"
    # Positive evidence: at least one log record carries the count.
    assert any("attempts=" in rec.getMessage() for rec in caplog.records), (
        "no log record surfaced the attempt count"
    )


# ---------------------------------------------------------------------
# Auth coverage table — mirrors the convention from test_children_api.
# ---------------------------------------------------------------------


_PROTECTED_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("DELETE", "/api/transcripts/abc", None),
    ("DELETE", "/api/transcripts", {"pin": GOOD_PIN}),
]


@pytest.mark.parametrize(("method", "path", "body"), _PROTECTED_ENDPOINTS)
def test_endpoints_require_parent_token(
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    """Every Step 22 destructive endpoint must 401 without a token."""
    response = client.request(method, path, json=body)
    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), _PROTECTED_ENDPOINTS)
def test_child_token_forbidden(
    client: TestClient,
    child_token: str,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    """Child-scope tokens must be rejected with 403 even on body-bearing DELETE."""
    headers = {"Authorization": f"Bearer {child_token}"}
    response = client.request(method, path, json=body, headers=headers)
    assert response.status_code == 403
