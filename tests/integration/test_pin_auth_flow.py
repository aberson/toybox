"""End-to-end tests for the parent PIN gate (Step 21).

Covers:

* ``GET /api/auth/parent/status`` reports first-run state.
* ``POST /api/auth/parent/setup`` validates digits-only / matching
  confirm / fresh-PIN preconditions, then issues a token.
* ``POST /api/auth/parent`` with the right PIN returns 200; wrong PIN
  returns 401 + attempts_remaining; 5 wrong → 423 + Retry-After.
* During lock, a correct PIN still returns 423.
* Lock expiry (driven by injecting ``time.monotonic``) re-opens the
  gate.
* Successful login resets the counter mid-window.
* :func:`toybox.core.bind_guard.pin_is_set` reflects DB state.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.api.auth import get_pin_rate_limiter
from toybox.api.auth_dep import get_auth_db
from toybox.app import create_app
from toybox.core.bind_guard import BindGuardError, check_bind_safe, pin_is_set
from toybox.core.pin import set_pin_hash
from toybox.core.pin_rate_limit import LOCK_DURATION_SECONDS, PinRateLimiter
from toybox.db.connection import connect

# A test PIN we re-use across cases. Digits-only, 4 chars, matches the
# spec's lower bound. Wrong-PIN cases use a different digit string.
GOOD_PIN = "1357"
WRONG_PIN = "9999"


@pytest.fixture
def limiter() -> PinRateLimiter:
    """Per-test rate limiter so module-singleton state can't leak.

    The default windows (5 min, 15 min lock) are kept here — tests
    drive the clock via the limiter's ``now`` parameter where needed,
    not via wall-clock sleeps.
    """
    return PinRateLimiter()


@pytest.fixture
def pin_app(db_path: Path, limiter: PinRateLimiter) -> Iterator[FastAPI]:
    """FastAPI app wired to the per-test DB + per-test rate limiter.

    Local fixture (rather than re-using the suite's ``app``) so the
    rate-limit override is scoped to this module and a flake here
    can't leak into another suite.
    """
    application = create_app()

    def _override_db() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    application.dependency_overrides[get_auth_db] = _override_db
    application.dependency_overrides[get_pin_rate_limiter] = lambda: limiter
    try:
        yield application
    finally:
        application.dependency_overrides.clear()


@pytest.fixture
def pin_client(pin_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(pin_app) as client:
        yield client


# ---- /api/auth/parent/status ------------------------------------------


def test_status_reports_unset_on_fresh_db(pin_client: TestClient) -> None:
    response = pin_client.get("/api/auth/parent/status")
    assert response.status_code == 200
    body = response.json()
    assert body == {"pin_set": False, "locked": False, "seconds_until_unlock": 0}


def test_status_reflects_set_pin(pin_client: TestClient, db_path: Path) -> None:
    conn = connect(db_path)
    try:
        set_pin_hash(conn, GOOD_PIN)
    finally:
        conn.close()
    response = pin_client.get("/api/auth/parent/status")
    assert response.status_code == 200
    assert response.json()["pin_set"] is True


# ---- /api/auth/parent/setup ------------------------------------------


def test_setup_first_run_returns_token(pin_client: TestClient, db_path: Path) -> None:
    response = pin_client.post(
        "/api/auth/parent/setup",
        json={"pin": GOOD_PIN, "confirm": GOOD_PIN},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["subject"] == {"kind": "parent"}
    assert isinstance(body["token"], str) and len(body["token"]) >= 32
    # And the hash is now stored.
    conn = connect(db_path)
    try:
        assert pin_is_set(conn) is True
    finally:
        conn.close()


def test_setup_rejects_mismatched_confirm(pin_client: TestClient) -> None:
    response = pin_client.post(
        "/api/auth/parent/setup",
        json={"pin": "1234", "confirm": "4321"},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    # Explicit raise → ``{code: ..., ...}`` dict for sibling-route
    # consistency; FastAPI's auto-validation 422s remain array-shaped.
    assert isinstance(detail, dict)
    assert detail == {"code": "pin_confirm_mismatch"}


def test_setup_rejects_non_digit_pin(pin_client: TestClient) -> None:
    response = pin_client.post(
        "/api/auth/parent/setup",
        json={"pin": "abcd", "confirm": "abcd"},
    )
    # Pydantic accepts the length but our custom validator rejects.
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail == {"code": "pin_format_invalid", "field": "pin"}


def test_setup_409_if_pin_already_set(pin_client: TestClient, db_path: Path) -> None:
    conn = connect(db_path)
    try:
        set_pin_hash(conn, GOOD_PIN)
    finally:
        conn.close()
    response = pin_client.post(
        "/api/auth/parent/setup",
        json={"pin": "0000", "confirm": "0000"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "pin_already_set"


# ---- /api/auth/parent (login) ----------------------------------------


def test_login_with_correct_pin_returns_token(pin_client: TestClient, db_path: Path) -> None:
    conn = connect(db_path)
    try:
        set_pin_hash(conn, GOOD_PIN)
    finally:
        conn.close()
    response = pin_client.post("/api/auth/parent", json={"pin": GOOD_PIN})
    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == {"kind": "parent"}
    assert isinstance(body["token"], str) and len(body["token"]) >= 32


def test_login_412_when_pin_not_set(pin_client: TestClient) -> None:
    response = pin_client.post("/api/auth/parent", json={"pin": GOOD_PIN})
    assert response.status_code == 412
    assert response.json()["detail"]["code"] == "pin_not_set"


def test_login_with_wrong_pin_returns_401(pin_client: TestClient, db_path: Path) -> None:
    conn = connect(db_path)
    try:
        set_pin_hash(conn, GOOD_PIN)
    finally:
        conn.close()
    response = pin_client.post("/api/auth/parent", json={"pin": WRONG_PIN})
    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail["code"] == "pin_invalid"
    assert detail["attempts_remaining"] == 4


# ---- rate-limit / lock -----------------------------------------------


def test_five_wrong_attempts_lock_with_retry_after(
    pin_client: TestClient,
    db_path: Path,
) -> None:
    conn = connect(db_path)
    try:
        set_pin_hash(conn, GOOD_PIN)
    finally:
        conn.close()
    last_response = None
    for _ in range(5):
        last_response = pin_client.post("/api/auth/parent", json={"pin": WRONG_PIN})
    assert last_response is not None
    assert last_response.status_code == 423
    assert last_response.headers.get("Retry-After") is not None
    detail = last_response.json()["detail"]
    assert detail["code"] == "pin_locked"
    # Lock window ≈ 900s; allow a small slop for the integer rounding +
    # in-test wall-clock skew.
    assert 800 <= detail["seconds_until_unlock"] <= 901


def test_correct_pin_during_lock_still_returns_423(
    pin_client: TestClient,
    db_path: Path,
) -> None:
    conn = connect(db_path)
    try:
        set_pin_hash(conn, GOOD_PIN)
    finally:
        conn.close()
    for _ in range(5):
        pin_client.post("/api/auth/parent", json={"pin": WRONG_PIN})
    response = pin_client.post("/api/auth/parent", json={"pin": GOOD_PIN})
    assert response.status_code == 423
    assert response.json()["detail"]["code"] == "pin_locked"


def test_status_reports_lock(pin_client: TestClient, db_path: Path) -> None:
    conn = connect(db_path)
    try:
        set_pin_hash(conn, GOOD_PIN)
    finally:
        conn.close()
    for _ in range(5):
        pin_client.post("/api/auth/parent", json={"pin": WRONG_PIN})
    response = pin_client.get("/api/auth/parent/status")
    body = response.json()
    assert body["locked"] is True
    assert body["seconds_until_unlock"] > 0


def test_lock_expiry_reopens_gate(
    pin_client: TestClient,
    db_path: Path,
    limiter: PinRateLimiter,
) -> None:
    """Drive the limiter past the lock window via real time arithmetic.

    The endpoint reads the limiter via the dep override and calls
    ``is_locked()`` with the default ``now=time.monotonic()``. Recording
    the 5 failures with ``now=`` set far enough in the past that
    ``monotonic() >= _locked_until`` exercises ``_drop_expired_lock``'s
    timestamp arithmetic end-to-end — no ``reset()`` shortcut.
    """
    import time

    conn = connect(db_path)
    try:
        set_pin_hash(conn, GOOD_PIN)
    finally:
        conn.close()
    # Anchor the failures to a monotonic timestamp so far in the past
    # that the lock expired before this test even started — when the
    # endpoint next calls ``is_locked()`` (which resolves now=monotonic()
    # internally), ``_drop_expired_lock`` clears the lock and the
    # counter, and the gate re-opens for the correct PIN.
    expired_anchor = time.monotonic() - LOCK_DURATION_SECONDS - 60.0
    for i in range(5):
        limiter.record_failed_attempt(now=expired_anchor + i)
    # Sanity check: at the anchor the limiter is locked, but the lock
    # expires before "now" (default monotonic).
    assert limiter.is_locked(now=expired_anchor + 1.0) is True
    assert limiter.is_locked() is False  # default now=time.monotonic()

    response = pin_client.post("/api/auth/parent", json={"pin": GOOD_PIN})
    assert response.status_code == 200, response.text
    # And the limiter state is fully cleared by _drop_expired_lock —
    # successful login resets remaining counter.
    assert limiter.status().attempts == 0


def test_successful_login_resets_counter(
    pin_client: TestClient,
    db_path: Path,
    limiter: PinRateLimiter,
) -> None:
    """4 wrong + 1 right + 5 wrong → the 5th wrong is when lock fires."""
    conn = connect(db_path)
    try:
        set_pin_hash(conn, GOOD_PIN)
    finally:
        conn.close()
    for _ in range(4):
        pin_client.post("/api/auth/parent", json={"pin": WRONG_PIN})
    # Counter is at 4. Right login resets to 0.
    success = pin_client.post("/api/auth/parent", json={"pin": GOOD_PIN})
    assert success.status_code == 200
    assert limiter.status().attempts == 0
    # Now 5 wrong attempts: only the 5th locks.
    statuses = []
    for _ in range(5):
        statuses.append(pin_client.post("/api/auth/parent", json={"pin": WRONG_PIN}))
    # First 4 wrong are 401; the 5th is 423.
    assert [s.status_code for s in statuses[:4]] == [401, 401, 401, 401]
    assert statuses[4].status_code == 423


def test_failed_attempt_logs_count_not_pin(
    pin_client: TestClient,
    db_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Spec invariant: log lines never carry the attempted PIN value."""
    conn = connect(db_path)
    try:
        set_pin_hash(conn, GOOD_PIN)
    finally:
        conn.close()
    # The pin we'll use as a wrong attempt — must not appear in any
    # log message.
    secret_attempt = "8642"
    with caplog.at_level(logging.WARNING, logger="toybox.api.auth"):
        pin_client.post("/api/auth/parent", json={"pin": secret_attempt})
    assert any("pin verification failed" in rec.getMessage() for rec in caplog.records)
    for record in caplog.records:
        msg = record.getMessage()
        assert secret_attempt not in msg, f"PIN leaked in log: {msg!r}"


# ---- bind_guard integration ------------------------------------------


def test_bind_guard_refuses_lan_without_pin(db_path: Path) -> None:
    """``check_bind_safe`` raises when no PIN is set + LAN host."""
    conn = connect(db_path)
    try:
        assert pin_is_set(conn) is False
    finally:
        conn.close()
    with pytest.raises(BindGuardError):
        check_bind_safe("0.0.0.0", pin_set=False)


def test_bind_guard_allows_lan_with_pin(db_path: Path) -> None:
    """Once a PIN is stored, LAN binding passes the guard."""
    conn = connect(db_path)
    try:
        set_pin_hash(conn, GOOD_PIN)
        assert pin_is_set(conn) is True
    finally:
        conn.close()
    # Real DB-backed flag flows through:
    conn = connect(db_path)
    try:
        check_bind_safe("0.0.0.0", pin_set=pin_is_set(conn))
    finally:
        conn.close()


# ---- Retry-After header ----------------------------------------------


def test_retry_after_header_matches_seconds_until_unlock(
    pin_client: TestClient,
    db_path: Path,
) -> None:
    conn = connect(db_path)
    try:
        set_pin_hash(conn, GOOD_PIN)
    finally:
        conn.close()
    last = None
    for _ in range(5):
        last = pin_client.post("/api/auth/parent", json={"pin": WRONG_PIN})
    assert last is not None and last.status_code == 423
    header = last.headers.get("Retry-After")
    assert header is not None
    seconds_in_body = last.json()["detail"]["seconds_until_unlock"]
    assert int(header) == seconds_in_body
    # And the body's seconds count is in the lock window neighbourhood.
    assert seconds_in_body > 0
    assert seconds_in_body <= int(LOCK_DURATION_SECONDS) + 1
