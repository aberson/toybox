"""Integration coverage for the /api/settings/image-gen-mode endpoints.

GET is unauthenticated (household read, mirrors the listening + audio
GETs). PUT is parent-scope; the brief explicitly requires this.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.api.auth_dep import get_auth_db
from toybox.api.image_gen_settings import get_db, get_publisher
from toybox.app import create_app
from toybox.core.auth import TokenScope, issue_token
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.ws.envelope import Envelope
from toybox.ws.topics import Topic


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
def captured_envelopes() -> list[Envelope]:
    return []


@pytest.fixture
def app_with_overrides(
    db_path: Path,
    captured_envelopes: list[Envelope],
) -> Iterator[FastAPI]:
    app = create_app()

    def _override_db() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    # The auth dep also opens its own DB connection; route it to the
    # same per-test DB so the parent token issued below resolves.
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_auth_db] = _override_db
    app.dependency_overrides[get_publisher] = (
        lambda: captured_envelopes.append
    )
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


def test_get_returns_default_for_fresh_db(client: TestClient) -> None:
    # Arrange — fresh migrated DB has the seeded ``cartoon`` row.
    # Act
    response = client.get("/api/settings/image-gen-mode")

    # Assert
    assert response.status_code == 200
    assert response.json() == {"mode": "cartoon"}


def test_put_sets_then_get_returns_new_value(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    # Act — flip to composite.
    put_response = client.put(
        "/api/settings/image-gen-mode",
        json={"mode": "composite"},
        headers=parent_headers,
    )

    # Assert
    assert put_response.status_code == 200
    assert put_response.json() == {"mode": "composite"}

    get_response = client.get("/api/settings/image-gen-mode")
    assert get_response.status_code == 200
    assert get_response.json() == {"mode": "composite"}


@pytest.mark.parametrize("bad_mode", ["foo", ""])
def test_put_invalid_mode_rejected(
    client: TestClient,
    parent_headers: dict[str, str],
    bad_mode: str,
) -> None:
    response = client.put(
        "/api/settings/image-gen-mode",
        json={"mode": bad_mode},
        headers=parent_headers,
    )
    assert response.status_code == 422


def test_put_requires_parent_scope_no_token(client: TestClient) -> None:
    # Act — no token → 401.
    response = client.put(
        "/api/settings/image-gen-mode",
        json={"mode": "composite"},
    )
    assert response.status_code == 401


def test_put_rejects_child_scope(
    client: TestClient,
    child_headers: dict[str, str],
) -> None:
    response = client.put(
        "/api/settings/image-gen-mode",
        json={"mode": "composite"},
        headers=child_headers,
    )
    assert response.status_code == 403


def test_put_emits_ws_envelope(
    client: TestClient,
    parent_headers: dict[str, str],
    captured_envelopes: list[Envelope],
) -> None:
    # Act
    response = client.put(
        "/api/settings/image-gen-mode",
        json={"mode": "composite"},
        headers=parent_headers,
    )

    # Assert
    assert response.status_code == 200
    assert len(captured_envelopes) == 1
    envelope = captured_envelopes[0]
    assert envelope.topic is Topic.image_gen_mode
    assert envelope.payload == {"mode": "composite"}


def test_get_does_not_require_token(client: TestClient) -> None:
    # The GET endpoint mirrors the listening / audio GETs — household
    # read; no scope guard.
    response = client.get("/api/settings/image-gen-mode")
    assert response.status_code == 200
