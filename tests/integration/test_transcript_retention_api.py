"""Integration coverage for the /api/settings/transcript-retention endpoints.

GET is unauthenticated (household read, mirrors the listening + audio
+ image-gen-mode GETs). PUT is parent-scope; bad values surface a 422
with a value-bearing error body so the frontend can render the valid
preset list without hard-coding it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.api.auth_dep import get_auth_db
from toybox.api.transcript_retention_settings import get_db
from toybox.app import create_app
from toybox.core.auth import TokenScope, issue_token
from toybox.core.transcript_retention import RETENTION_SECONDS_VALID
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


def test_get_returns_default_when_seed_absent(
    db_path: Path,
    client: TestClient,
) -> None:
    """GET returns 60 even when the seed row has been deleted (legacy DB shape)."""
    # Arrange — wipe the seeded settings row to simulate a legacy DB.
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "DELETE FROM settings WHERE key = 'transcript_retention_seconds'"
            )
    finally:
        conn.close()

    # Act
    response = client.get("/api/settings/transcript-retention")

    # Assert
    assert response.status_code == 200
    assert response.json() == {"seconds": 60}


def test_get_returns_seeded_default_on_fresh_db(client: TestClient) -> None:
    """Fresh migrated DB has the seed row at 60."""
    response = client.get("/api/settings/transcript-retention")
    assert response.status_code == 200
    assert response.json() == {"seconds": 60}


@pytest.mark.parametrize("preset", [180, 900], ids=["canonical_180", "boundary_900"])
def test_put_round_trip_representative_presets(
    client: TestClient,
    parent_headers: dict[str, str],
    preset: int,
) -> None:
    """PUT representative presets → 200 echoes value → GET reads it back.

    Unit-level round-trip already covers every preset; integration only
    needs to prove the wire path works for one canonical + one boundary.
    """
    put_response = client.put(
        "/api/settings/transcript-retention",
        json={"seconds": preset},
        headers=parent_headers,
    )
    assert put_response.status_code == 200
    assert put_response.json() == {"seconds": preset}

    get_response = client.get("/api/settings/transcript-retention")
    assert get_response.status_code == 200
    assert get_response.json() == {"seconds": preset}


def test_put_invalid_int_returns_422_with_exact_detail(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Out-of-set int → 422 with the canonical list in the detail body."""
    response = client.put(
        "/api/settings/transcript-retention",
        json={"seconds": 120},
        headers=parent_headers,
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "error": "invalid_retention",
            "valid": sorted(RETENTION_SECONDS_VALID),
        }
    }


@pytest.mark.parametrize(
    "body",
    [{"seconds": "not a number"}, {}],
    ids=["non_int", "missing_field"],
)
def test_put_bad_body_returns_422(
    client: TestClient,
    parent_headers: dict[str, str],
    body: dict[str, object],
) -> None:
    """Non-int / missing body → FastAPI's default 422 (Pydantic validation)."""
    response = client.put(
        "/api/settings/transcript-retention",
        json=body,
        headers=parent_headers,
    )
    assert response.status_code == 422


def test_put_without_token_returns_401(client: TestClient) -> None:
    """No bearer token → 401 (RequireScope default for missing creds)."""
    response = client.put(
        "/api/settings/transcript-retention",
        json={"seconds": 180},
    )
    assert response.status_code == 401


def test_put_with_child_token_returns_403(
    client: TestClient,
    child_headers: dict[str, str],
) -> None:
    """Child-scope token cannot change a household setting."""
    response = client.put(
        "/api/settings/transcript-retention",
        json={"seconds": 180},
        headers=child_headers,
    )
    assert response.status_code == 403
