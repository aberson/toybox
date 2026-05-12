"""Integration coverage for the /api/settings/play-target-depth endpoints.

Household-scoped ``play_target_depth`` setting. Canonical set is
``{1, 3, 5}`` with default ``3``. GET is unauthenticated (household
read, mirrors the listening + audio + image-gen-mode + transcript-
retention GETs). PUT is parent-scope; bad values surface a 422.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.api.auth_dep import get_auth_db
from toybox.api.play_target_depth_settings import get_db
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
        token = issue_token(
            conn, TokenScope.child, child_session_label="child-1"
        ).token
    finally:
        conn.close()
    return {"Authorization": f"Bearer {token}"}


def test_get_returns_default_on_fresh_db(client: TestClient) -> None:
    """Fresh migrated DB → GET returns the canonical default (3)."""
    response = client.get("/api/settings/play-target-depth")
    assert response.status_code == 200
    assert response.json() == {"value": 3}


@pytest.mark.parametrize("preset", [1, 5], ids=["min_1", "max_5"])
def test_put_valid_value_persists_and_round_trips(
    client: TestClient,
    parent_headers: dict[str, str],
    preset: int,
) -> None:
    """PUT representative presets → 200 echoes value → GET reads it back."""
    put_response = client.put(
        "/api/settings/play-target-depth",
        json={"value": preset},
        headers=parent_headers,
    )
    assert put_response.status_code == 200
    assert put_response.json() == {"value": preset}

    get_response = client.get("/api/settings/play-target-depth")
    assert get_response.status_code == 200
    assert get_response.json() == {"value": preset}


def test_put_invalid_value_returns_422(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Out-of-set int → 422; subsequent GET returns the unchanged default."""
    response = client.put(
        "/api/settings/play-target-depth",
        json={"value": 2},
        headers=parent_headers,
    )
    assert response.status_code == 422

    get_response = client.get("/api/settings/play-target-depth")
    assert get_response.status_code == 200
    assert get_response.json() == {"value": 3}


def test_put_non_integer_value_returns_422(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Non-int body → 422 (FastAPI / Pydantic validation)."""
    response = client.put(
        "/api/settings/play-target-depth",
        json={"value": "abc"},
        headers=parent_headers,
    )
    assert response.status_code == 422


def test_put_without_token_returns_401(client: TestClient) -> None:
    """No bearer token → 401 (RequireScope default for missing creds)."""
    response = client.put(
        "/api/settings/play-target-depth",
        json={"value": 5},
    )
    assert response.status_code == 401


def test_put_with_child_token_returns_403(
    client: TestClient,
    child_headers: dict[str, str],
) -> None:
    """Child-scope token cannot change a household setting → 403."""
    response = client.put(
        "/api/settings/play-target-depth",
        json={"value": 5},
        headers=child_headers,
    )
    assert response.status_code == 403
