"""Integration coverage for the Step 4 listening-mode state machine."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.api.listening import get_db, get_publisher
from toybox.app import create_app
from toybox.core.listening import ListeningMode, current_mode, set_mode
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.ws.envelope import Envelope, build_envelope
from toybox.ws.topics import Topic


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a fresh, migrated connection; close on teardown (Windows-safe)."""
    conn = connect(tmp_path / "toybox.db")
    try:
        run_migrations(conn)
        yield conn
    finally:
        conn.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Iterator[Path]:
    """Yield a fresh, migrated DB path for HTTP tests; teardown closes the bootstrap conn."""
    path = tmp_path / "toybox.db"
    conn = connect(path)
    try:
        run_migrations(conn)
    finally:
        conn.close()
    yield path


@pytest.fixture
def captured_envelopes() -> list[Envelope]:
    """Mutable list the publisher override appends each emitted envelope into."""
    return []


@pytest.fixture
def app_with_overrides(
    db_path: Path,
    captured_envelopes: list[Envelope],
) -> Iterator[FastAPI]:
    """Yield a fresh app with ``get_db`` and ``get_publisher`` overridden.

    The publisher override is the captured list's ``append`` method, which
    matches the ``Publisher = Callable[[Envelope], None]`` shape exactly
    — no ``type: ignore`` and no stashing on ``app.state`` required.
    """
    app = create_app()

    def _override_db() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_publisher] = lambda: captured_envelopes.append
    try:
        yield app
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client(app_with_overrides: FastAPI) -> Iterator[TestClient]:
    """Yield a TestClient bound to the override-wired app."""
    with TestClient(app_with_overrides) as test_client:
        yield test_client


def test_default_mode_is_3_after_migration(db: sqlite3.Connection) -> None:
    assert current_mode(db) is ListeningMode.DEFAULT


def test_set_mode_persists(db: sqlite3.Connection, tmp_path: Path) -> None:
    set_mode(db, 5)
    db.close()

    reopened = connect(tmp_path / "toybox.db")
    try:
        assert current_mode(reopened) is ListeningMode.INTENSE
    finally:
        reopened.close()


def test_set_mode_emits_envelope(db: sqlite3.Connection) -> None:
    captured: list[Envelope] = []
    set_mode(db, 5, publisher=captured.append)

    assert len(captured) == 1
    envelope = captured[0]
    assert envelope.topic is Topic.listening_mode
    assert envelope.payload == {"mode": 5}
    assert envelope.schema_version == 1
    assert envelope.ts.tzinfo is not None  # UTC, not naive


def test_envelope_serializes_topic_as_dotted_string() -> None:
    env = build_envelope(topic=Topic.listening_mode, payload={"mode": 3})
    payload = env.model_dump_json()
    assert '"topic":"listening.mode"' in payload


@pytest.mark.parametrize("invalid", [0, 6, -1, 99])
def test_set_mode_invalid_raises(db: sqlite3.Connection, invalid: int) -> None:
    with pytest.raises(ValueError):
        set_mode(db, invalid)


def test_set_mode_no_publisher_ok(db: sqlite3.Connection) -> None:
    result = set_mode(db, 4, publisher=None)
    assert result is ListeningMode.HIGH
    assert current_mode(db) is ListeningMode.HIGH


def test_current_mode_missing_row_falls_back(
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TOYBOX_DEFAULT_MODE", raising=False)
    db.execute("DELETE FROM settings WHERE key = 'listening_mode'")
    assert current_mode(db) is ListeningMode.DEFAULT


def test_current_mode_env_override_when_row_missing(
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOYBOX_DEFAULT_MODE", "2")
    db.execute("DELETE FROM settings WHERE key = 'listening_mode'")
    assert current_mode(db) is ListeningMode.LOW


def test_current_mode_invalid_persisted_value_raises(db: sqlite3.Connection) -> None:
    db.execute("UPDATE settings SET value = '7' WHERE key = 'listening_mode'")
    with pytest.raises(ValueError):
        current_mode(db)


def test_get_mode_endpoint(client: TestClient) -> None:
    response = client.get("/api/listening/mode")
    assert response.status_code == 200
    assert response.json() == {"mode": 3}


def test_put_mode_endpoint(client: TestClient) -> None:
    put_response = client.put("/api/listening/mode", json={"mode": 5})
    assert put_response.status_code == 200
    assert put_response.json() == {"mode": 5}

    get_response = client.get("/api/listening/mode")
    assert get_response.json() == {"mode": 5}


def test_put_mode_emits_envelope_via_endpoint(
    client: TestClient,
    captured_envelopes: list[Envelope],
) -> None:
    response = client.put("/api/listening/mode", json={"mode": 4})
    assert response.status_code == 200

    assert len(captured_envelopes) == 1
    envelope = captured_envelopes[0]
    assert envelope.topic is Topic.listening_mode
    assert envelope.payload == {"mode": 4}


@pytest.mark.parametrize("bad_mode", [0, 6, 9, -1])
def test_put_mode_invalid_returns_422(client: TestClient, bad_mode: int) -> None:
    response = client.put("/api/listening/mode", json={"mode": bad_mode})
    assert response.status_code == 422
