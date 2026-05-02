"""Auth scaffolding: ``/api/auth/parent``, ``/api/auth/pair``, and validation."""

from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from toybox.core.auth import (
    DEFAULT_TOKEN_TTL,
    TokenError,
    TokenScope,
    hash_token,
    issue_token,
    revoke_token,
    validate_token,
)
from toybox.db.connection import connect


def test_post_parent_returns_token(client: TestClient) -> None:
    response = client.post("/api/auth/parent")
    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == {"kind": "parent"}
    assert isinstance(body["token"], str) and len(body["token"]) >= 32
    assert body["expires_at"] > time.time()


def test_post_parent_survives_repeated_requests(client: TestClient) -> None:
    """Regression: ``POST /api/auth/parent`` must not 500 on repeated calls.

    Iter-1 cached its SQLite connection without ``check_same_thread=False``,
    which tripped ``sqlite3.ProgrammingError: SQLite objects created in a
    thread can only be used in that same thread`` once FastAPI's threadpool
    moved the sync generator's teardown to a different worker than the
    setup. Five sequential calls is enough to exercise that flip in
    practice; we also stress it with a thread pool below.
    """
    for _ in range(5):
        response = client.post("/api/auth/parent")
        assert response.status_code == 200, response.text


def test_post_parent_concurrent_requests_all_succeed(client: TestClient) -> None:
    """Regression: concurrent calls from a real ``ThreadPoolExecutor``.

    Each request goes through a different FastAPI threadpool worker so
    we cover the cross-thread close path (the iter-1 bug shape) more
    robustly than the sequential test alone.
    """
    from concurrent.futures import ThreadPoolExecutor

    def _hit() -> int:
        return client.post("/api/auth/parent").status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _i: _hit(), range(16)))

    assert all(status == 200 for status in statuses), statuses


def test_post_pair_requires_existing_child(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/auth/pair",
        json={"child_id": "no-such-child"},
        headers=parent_headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "child_not_found"


def test_post_pair_requires_parent_auth(
    client: TestClient,
    db_path: Path,
) -> None:
    """Without a parent-scope token, ``/api/auth/pair`` is 401."""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children (id, display_name) VALUES (?, ?)",
                ("c-1", "Mini"),
            )
    finally:
        conn.close()

    response = client.post("/api/auth/pair", json={"child_id": "c-1"})
    assert response.status_code == 401


def test_post_pair_rejects_child_scope(
    client: TestClient,
    child_token: str,
) -> None:
    """A child-scope token cannot mint another child-scope token."""
    response = client.post(
        "/api/auth/pair",
        json={"child_id": "child-1"},
        headers={"Authorization": f"Bearer {child_token}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "auth_scope_forbidden"


def test_post_pair_issues_child_scope_token(
    client: TestClient,
    db_path: Path,
    parent_headers: dict[str, str],
) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children (id, display_name) VALUES (?, ?)",
                ("c-1", "Mini"),
            )
    finally:
        conn.close()

    response = client.post(
        "/api/auth/pair",
        json={"child_id": "c-1"},
        headers=parent_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == {"kind": "child", "id": "c-1"}
    assert isinstance(body["token"], str)

    # Round-trip: validate the issued token and confirm it carries the
    # ``child_session_label`` so downstream attribution can recover the
    # child id.
    conn = connect(db_path)
    try:
        subject = validate_token(conn, body["token"])
    finally:
        conn.close()
    assert subject.scope is TokenScope.child
    assert subject.child_session_label == "c-1"


def test_validate_token_round_trip(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        issued = issue_token(conn, TokenScope.parent)
        assert hash_token(issued.token) == issued.token_hash
        subject = validate_token(conn, issued.token)
        assert subject.scope is TokenScope.parent
    finally:
        conn.close()


def test_validate_unknown_token_raises(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        with pytest.raises(TokenError):
            validate_token(conn, "definitely-not-issued")
    finally:
        conn.close()


def test_validate_revoked_token_raises(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        issued = issue_token(conn, TokenScope.parent)
        assert revoke_token(conn, issued.token_hash) is True
        with pytest.raises(TokenError):
            validate_token(conn, issued.token)
    finally:
        conn.close()


def test_validate_expired_token_raises(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        issued = issue_token(conn, TokenScope.parent, ttl=timedelta(seconds=-1))
        with pytest.raises(TokenError):
            validate_token(conn, issued.token)
    finally:
        conn.close()


def test_default_ttl_is_24_hours() -> None:
    assert DEFAULT_TOKEN_TTL == timedelta(hours=24)
