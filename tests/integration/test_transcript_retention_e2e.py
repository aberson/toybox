"""Phase I Step I2 — end-to-end coverage for the retention helper + sweep + filter contracts.

This is the contract test that locks the helper-and-API pieces together
into a single happy-path assertion. If any piece drifts — sweep uses a
different cutoff format than filter-on-read, helper returns a value the
SQL doesn't honour, in-flight rows leak past the read filter — this
test breaks.

Flow:

1. PUT ``/api/settings/transcript-retention`` with 60s (the canonical
   default, but we set it explicitly so the test is hermetic against
   any future default change).
2. Insert three fixture rows using the pipeline-format helper:
   * ``t-fresh``    — ``ended_at = now - 30s`` (inside the 60s window)
   * ``t-expired``  — ``ended_at = now - 90s`` (past the 60s window)
   * ``t-in-flight`` — ``ended_at IS NULL`` (still being spoken)
3. Call :func:`sweep_expired_transcripts` directly with the current
   wall-clock ``now``. ``t-expired`` should disappear; the other two
   should remain.
4. GET ``/api/transcripts`` and assert the response contains
   **exactly one** row — the ``t-fresh`` row. ``t-expired`` was
   deleted by the sweep; ``t-in-flight`` is filtered out by the
   read-side ``ended_at IS NOT NULL`` clause (in-flight rows are
   surfaced via the WS push, not this endpoint).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.api.auth_dep import get_auth_db
from toybox.api.transcript_retention_settings import get_db as get_retention_db
from toybox.api.transcripts import get_transcripts_db
from toybox.app import create_app
from toybox.core.auth import TokenScope, issue_token
from toybox.core.transcript_retention import (
    _format_ended_at_cutoff,
    sweep_expired_transcripts,
)
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "toybox.db"
    conn = connect(path)
    try:
        run_migrations(conn)
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                ("s1", "2026-01-01T00:00:00Z"),
            )
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

    for dep in (get_retention_db, get_auth_db, get_transcripts_db):
        app.dependency_overrides[dep] = _override_db
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


def _insert_row(
    db_path: Path,
    *,
    row_id: str,
    ended_at: str | None,
    started_at: str,
) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO transcripts "
                "(id, session_id, mic_id, started_at, ended_at, text, "
                " confidence, language) "
                "VALUES (?, 's1', NULL, ?, ?, ?, ?, ?)",
                (row_id, started_at, ended_at, f"text {row_id}", 0.7, "en"),
            )
    finally:
        conn.close()


def test_e2e_retention_sweep_and_filter(
    client: TestClient,
    db_path: Path,
    parent_headers: dict[str, str],
) -> None:
    # Step 1 — pin retention to 60s via the I1 endpoint.
    put = client.put(
        "/api/settings/transcript-retention",
        json={"seconds": 60},
        headers=parent_headers,
    )
    assert put.status_code == 200, put.text
    assert put.json() == {"seconds": 60}

    # Step 2 — insert three fixture rows using the pipeline-format
    # helper. Pin ``now`` so the assertion is hermetic against
    # clock skew between the test body and the API request.
    now = datetime.now(UTC)
    _insert_row(
        db_path,
        row_id="t-fresh",
        ended_at=_format_ended_at_cutoff(now - timedelta(seconds=30)),
        started_at=_format_ended_at_cutoff(now - timedelta(seconds=31)),
    )
    _insert_row(
        db_path,
        row_id="t-expired",
        ended_at=_format_ended_at_cutoff(now - timedelta(seconds=90)),
        started_at=_format_ended_at_cutoff(now - timedelta(seconds=91)),
    )
    _insert_row(
        db_path,
        row_id="t-in-flight",
        ended_at=None,
        started_at=_format_ended_at_cutoff(now - timedelta(seconds=2)),
    )

    # Step 3 — run the sweep directly. ``t-expired`` should disappear;
    # ``t-fresh`` and ``t-in-flight`` should both survive.
    sweep_conn = connect(db_path, check_same_thread=False)
    try:
        deleted = sweep_expired_transcripts(sweep_conn, now)
    finally:
        sweep_conn.close()
    assert deleted == 1

    # Confirm at the DB layer before checking the API response.
    verify_conn = connect(db_path)
    try:
        ids = {
            row[0]
            for row in verify_conn.execute("SELECT id FROM transcripts").fetchall()
        }
    finally:
        verify_conn.close()
    assert ids == {"t-fresh", "t-in-flight"}

    # Step 4 — GET /api/transcripts. The list endpoint must return
    # exactly one row (``t-fresh``); ``t-in-flight`` is filtered out
    # by the ``ended_at IS NOT NULL`` clause.
    response = client.get("/api/transcripts")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1, f"expected 1 row, got {items}"
    assert items[0]["id"] == "t-fresh"
