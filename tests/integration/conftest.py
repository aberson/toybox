"""Shared fixtures for the Step 8 integration suite.

Each test gets its own SQLite file (``tmp_path / 'toybox.db'``), an
isolated :class:`toybox.core.pubsub.PubSub` hub, and a FastAPI
``TestClient`` whose ``get_db`` / ``get_pubsub`` dependencies are
overridden to point at the per-test instances. The fixtures are
deliberately scope-``function`` so concurrent tests can't contend on a
shared DB or hub.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.api.activities import get_activities_db
from toybox.api.auth_dep import get_auth_db
from toybox.api.listening import get_db as get_listening_db
from toybox.api.transcripts import get_transcripts_db
from toybox.app import create_app
from toybox.core.auth import TokenScope, issue_token
from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.ws.server import get_pubsub, get_ws_db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a fresh, migrated SQLite file and return its path."""
    path = tmp_path / "toybox.db"
    conn = connect(path)
    try:
        run_migrations(conn)
    finally:
        conn.close()
    return path


@pytest.fixture
def pubsub() -> PubSub:
    """Per-test pubsub hub (smaller queue cap so backpressure tests are quick)."""
    return PubSub(max_per_subscriber=32, coalesce_window_ms=0)


@pytest.fixture
def app(db_path: Path, pubsub: PubSub) -> Iterator[FastAPI]:
    """FastAPI app with all DB dependencies routed to ``db_path``."""
    application = create_app()

    def _override_db() -> Iterator[sqlite3.Connection]:
        # Match the production deps: FastAPI's threadpool may schedule
        # generator setup, the handler body, and teardown on different
        # worker threads, so the connection must allow cross-thread use.
        conn = connect(db_path, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    def _override_ws_db() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    for dep in (
        get_listening_db,
        get_auth_db,
        get_activities_db,
        get_transcripts_db,
    ):
        application.dependency_overrides[dep] = _override_db
    application.dependency_overrides[get_ws_db] = _override_ws_db
    application.dependency_overrides[get_pubsub] = lambda: pubsub
    try:
        yield application
    finally:
        application.dependency_overrides.clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """``TestClient`` bound to the override-wired app."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def parent_token(db_path: Path) -> str:
    """Issue a parent-scope token directly against the test DB."""
    conn = connect(db_path)
    try:
        return issue_token(conn, TokenScope.parent).token
    finally:
        conn.close()


@pytest.fixture
def child_token(db_path: Path) -> str:
    """Insert a child profile + child-scope token, return the plaintext."""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO children (id, display_name) VALUES (?, ?)",
                ("child-1", "Test Child"),
            )
        return issue_token(conn, TokenScope.child, child_session_label="child-1").token
    finally:
        conn.close()


@pytest.fixture
def parent_headers(parent_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {parent_token}"}
