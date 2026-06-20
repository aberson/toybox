"""Integration coverage for the /api/settings/parent-involvement endpoints.

GET is unauthenticated (household read, mirrors the spoken-text-limit
GET). PUT is parent-scope; bad values surface a 422 with a value-bearing
error body so the frontend can render the valid option list without
hard-coding it. Phase W Step W1 true-stub: PERSIST ONLY.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.api.auth_dep import get_auth_db
from toybox.api.parent_involvement_settings import get_db
from toybox.app import create_app
from toybox.core.auth import TokenScope, issue_token
from toybox.core.parent_involvement import PARENT_INVOLVEMENT_VALID
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


def test_get_returns_default_when_seed_absent(
    db_path: Path,
    client: TestClient,
) -> None:
    """GET returns medium even when the seed row has been deleted (legacy DB)."""
    # Arrange — wipe the seeded settings row to simulate a legacy DB.
    conn = connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM settings WHERE key = 'parent_involvement'")
    finally:
        conn.close()

    # Act
    response = client.get("/api/settings/parent-involvement")

    # Assert
    assert response.status_code == 200
    assert response.json() == {"value": "medium"}


def test_get_returns_seeded_default_on_fresh_db(client: TestClient) -> None:
    """Fresh migrated DB has the seed row at medium."""
    response = client.get("/api/settings/parent-involvement")
    assert response.status_code == 200
    assert response.json() == {"value": "medium"}


@pytest.mark.parametrize("value", ["low", "high"], ids=["low", "high"])
def test_put_round_trip_representative_values(
    client: TestClient,
    parent_headers: dict[str, str],
    value: str,
) -> None:
    """PUT representative values -> 200 echoes value -> GET reads it back.

    Unit-level round-trip already covers every value; integration only
    needs to prove the wire path works for two representative values.
    """
    put_response = client.put(
        "/api/settings/parent-involvement",
        json={"value": value},
        headers=parent_headers,
    )
    assert put_response.status_code == 200
    assert put_response.json() == {"value": value}

    get_response = client.get("/api/settings/parent-involvement")
    assert get_response.status_code == 200
    assert get_response.json() == {"value": value}


def test_put_invalid_value_returns_422_with_exact_detail(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Out-of-set value -> 422 with the canonical list in the detail body."""
    response = client.put(
        "/api/settings/parent-involvement",
        json={"value": "extreme"},
        headers=parent_headers,
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "error": "invalid_parent_involvement",
            "valid": sorted(PARENT_INVOLVEMENT_VALID),
        }
    }


def test_put_missing_field_returns_422(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Missing body field -> FastAPI's default 422 (Pydantic validation)."""
    response = client.put(
        "/api/settings/parent-involvement",
        json={},
        headers=parent_headers,
    )
    assert response.status_code == 422


def test_put_without_token_returns_401(client: TestClient) -> None:
    """No bearer token -> 401 (RequireScope default for missing creds)."""
    response = client.put(
        "/api/settings/parent-involvement",
        json={"value": "high"},
    )
    assert response.status_code == 401


def test_put_with_child_token_returns_403(
    client: TestClient,
    child_headers: dict[str, str],
) -> None:
    """Child-scope token cannot change a household setting."""
    response = client.put(
        "/api/settings/parent-involvement",
        json={"value": "high"},
        headers=child_headers,
    )
    assert response.status_code == 403
