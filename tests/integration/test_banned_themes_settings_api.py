"""Integration coverage for the /api/settings/banned-themes endpoints.

Phase H Step H4 — household-global banned-themes setting. GET is
unauthenticated (matches the image-gen-mode GET; the value is just
metadata the parent UI surfaces). PUT is parent-scope; the operator's
explicit configuration.

The endpoints are deliberately the minimum surface — no WebSocket
broadcast, no rate limiting beyond the existing parent-token middleware.
The escalation pipeline reads the value per-request; a stale value on
an open client resolves on the next propose without an explicit push.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.api.auth_dep import get_auth_db
from toybox.api.banned_themes_settings import get_db
from toybox.app import create_app
from toybox.core.auth import TokenScope, issue_token
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "toybox.db"
    conn = connect(path)
    try:
        run_migrations(conn)
    finally:
        conn.close()
    return path


@pytest.fixture
def app_with_overrides(db_path: Path) -> Iterator[FastAPI]:
    app = create_app()

    def _override_db() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    # Both the banned-themes router and the auth dep open their own
    # connections; route both at the per-test DB.
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_auth_db] = _override_db
    try:
        yield app
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client(app_with_overrides: FastAPI) -> Iterator[TestClient]:
    with TestClient(app_with_overrides) as test_client:
        yield test_client


@pytest.fixture
def parent_headers(db_path: Path) -> dict[str, str]:
    conn = connect(db_path)
    try:
        token = issue_token(conn, TokenScope.parent).token
    finally:
        conn.close()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def child_headers(db_path: Path) -> dict[str, str]:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children (id, display_name) VALUES (?, ?)",
                ("child-1", "Test Child"),
            )
        token = issue_token(conn, TokenScope.child, child_session_label="child-1").token
    finally:
        conn.close()
    return {"Authorization": f"Bearer {token}"}


def test_get_returns_null_on_fresh_db(client: TestClient) -> None:
    """A fresh DB has no global banned-themes row; GET returns ``null``."""
    response = client.get("/api/settings/banned-themes")
    assert response.status_code == 200
    assert response.json() == {"themes": None}


def test_put_then_get_round_trip(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    put_response = client.put(
        "/api/settings/banned-themes",
        json={"themes": "monsters, spiders"},
        headers=parent_headers,
    )
    assert put_response.status_code == 200
    assert put_response.json() == {"themes": "monsters, spiders"}

    get_response = client.get("/api/settings/banned-themes")
    assert get_response.status_code == 200
    assert get_response.json() == {"themes": "monsters, spiders"}


def test_put_null_clears_row(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    # Set a value, then null it.
    client.put(
        "/api/settings/banned-themes",
        json={"themes": "monsters"},
        headers=parent_headers,
    )
    put_response = client.put(
        "/api/settings/banned-themes",
        json={"themes": None},
        headers=parent_headers,
    )
    assert put_response.status_code == 200
    assert put_response.json() == {"themes": None}

    get_response = client.get("/api/settings/banned-themes")
    assert get_response.json() == {"themes": None}


def test_put_empty_string_clears_row(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """An empty / whitespace-only string deletes the row.

    Matches the contract of
    :func:`toybox.core.banned_themes.set_banned_themes_global`: any
    value that is empty after strip is "no global ban list", which
    reads back as ``None``.
    """
    client.put(
        "/api/settings/banned-themes",
        json={"themes": "monsters"},
        headers=parent_headers,
    )
    put_response = client.put(
        "/api/settings/banned-themes",
        json={"themes": "   "},
        headers=parent_headers,
    )
    assert put_response.status_code == 200
    assert put_response.json() == {"themes": None}


def test_put_value_round_trips_verbatim(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Storage preserves the operator's exact textarea contents.

    No server-side normalisation — split/trim/lowercase is the caller's
    job for display. The raw string makes the round-trip without
    surprise (whitespace, case, ordering all preserved).
    """
    value = "Monsters,  SPIDERS, ghosts  "
    put_response = client.put(
        "/api/settings/banned-themes",
        json={"themes": value},
        headers=parent_headers,
    )
    assert put_response.status_code == 200
    assert put_response.json() == {"themes": value}

    get_response = client.get("/api/settings/banned-themes")
    assert get_response.json() == {"themes": value}


def test_put_requires_parent_scope_no_token(client: TestClient) -> None:
    response = client.put(
        "/api/settings/banned-themes",
        json={"themes": "monsters"},
    )
    assert response.status_code == 401


def test_put_rejects_child_scope(
    client: TestClient,
    child_headers: dict[str, str],
) -> None:
    response = client.put(
        "/api/settings/banned-themes",
        json={"themes": "monsters"},
        headers=child_headers,
    )
    assert response.status_code == 403


def test_get_does_not_require_token(client: TestClient) -> None:
    """GET mirrors the image-gen-mode GET — household read, no scope guard."""
    response = client.get("/api/settings/banned-themes")
    assert response.status_code == 200
