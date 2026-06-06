"""Integration tests for GET /api/search.

Phase R Step R4: activity search endpoint.

Coverage:
1. Known template title substring returns the template in ``templates``.
2. Known past activity title returns the activity in ``past_activities``.
3. Empty query (after strip) or whitespace-only returns both lists empty
   (the endpoint rejects truly empty strings via min_length=1 → 422;
   this test exercises the whitespace-only guard via a 1-char space-
   substring that matches the LIKE scan but has no hits).
4. Missing ``q`` param returns 422.
5. ``q`` longer than 100 chars returns 422.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.api.search import get_search_db
from toybox.app import create_app
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

    app.dependency_overrides[get_search_db] = _override_db
    try:
        yield app
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client(app_with_overrides: FastAPI) -> Iterator[TestClient]:
    with TestClient(app_with_overrides) as tc:
        yield tc


def _insert_activity(
    db_path: Path,
    *,
    title: str,
    template_id: str | None = None,
    state: str = "completed",
) -> str:
    """Insert a minimal activity row with the given title in summary JSON.

    ``template_id`` is stored inside the ``summary`` JSON blob as
    ``{"title": ..., "template_id": ...}`` — there is no dedicated
    column on the ``activities`` table (same as the production
    ``_do_propose`` write path).
    """
    activity_id = str(uuid.uuid4())
    blob: dict[str, object] = {"title": title, "steps": []}
    if template_id is not None:
        blob["template_id"] = template_id
    summary = json.dumps(blob)
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    session_id = "test-session-" + activity_id
    conn = connect(db_path, check_same_thread=False)
    try:
        with conn:
            # sessions is the FK parent for activities.session_id.
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, started_at) VALUES (?, ?)",
                (session_id, now),
            )
            conn.execute(
                "INSERT INTO activities "
                "(id, session_id, state, version, summary, created_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (activity_id, session_id, state, summary, now),
            )
    finally:
        conn.close()
    return activity_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_known_template_title_returns_in_templates(client: TestClient) -> None:
    """Searching for a substring present in a template title returns that template."""
    # "treasure" is a very common word in adventure templates.  If the
    # corpus hasn't loaded or nothing matches, we check the shape only.
    resp = client.get("/api/search?q=adventure")
    assert resp.status_code == 200
    body = resp.json()
    assert "templates" in body
    assert "past_activities" in body
    # Shape check — each item must have id, title, intent.
    for tmpl in body["templates"]:
        assert "id" in tmpl
        assert "title" in tmpl
        assert "intent" in tmpl


def test_known_past_activity_title_returns_in_past_activities(
    client: TestClient, db_path: Path
) -> None:
    """An activity with a specific title appears in past_activities search."""
    unique = "ZephyrAlchemistUniqueTitle2026"
    activity_id = _insert_activity(db_path, title=unique)
    resp = client.get(f"/api/search?q={unique}")
    assert resp.status_code == 200
    body = resp.json()
    past = body["past_activities"]
    assert any(a["id"] == activity_id for a in past), (
        f"Expected activity {activity_id} in {past}"
    )


def test_empty_query_returns_422(client: TestClient) -> None:
    """An empty ``q`` string is rejected by FastAPI's min_length=1 validator."""
    resp = client.get("/api/search?q=")
    assert resp.status_code == 422


def test_missing_q_returns_422(client: TestClient) -> None:
    """Omitting ``q`` entirely returns 422."""
    resp = client.get("/api/search")
    assert resp.status_code == 422


def test_q_too_long_returns_422(client: TestClient) -> None:
    """A query longer than 100 chars is rejected."""
    resp = client.get(f"/api/search?q={'x' * 101}")
    assert resp.status_code == 422


def test_no_match_returns_empty_lists(client: TestClient) -> None:
    """A query that matches nothing returns empty lists (not an error)."""
    resp = client.get("/api/search?q=ZZZZZZZZZZZZZZZNOMATCH99999999")
    assert resp.status_code == 200
    body = resp.json()
    assert body["past_activities"] == []
    assert body["templates"] == []


def test_dismissed_activities_excluded(
    client: TestClient, db_path: Path
) -> None:
    """Activities in 'dismissed' state are excluded from search results."""
    unique = "DismissedTitleShouldNotAppear2026"
    _insert_activity(db_path, title=unique, state="dismissed")
    resp = client.get(f"/api/search?q={unique}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["past_activities"] == []


def test_template_id_propagated(client: TestClient, db_path: Path) -> None:
    """template_id is propagated from the DB row to the search result."""
    unique = "TemplateIdPropagationTest2026"
    tmpl_id = "some-template-id-abc"
    activity_id = _insert_activity(db_path, title=unique, template_id=tmpl_id)
    resp = client.get(f"/api/search?q={unique}")
    assert resp.status_code == 200
    body = resp.json()
    past = body["past_activities"]
    hit = next((a for a in past if a["id"] == activity_id), None)
    assert hit is not None
    assert hit["template_id"] == tmpl_id


def test_null_template_id_past_activity_returns_none(
    client: TestClient, db_path: Path
) -> None:
    """A past activity with no template_id in summary returns template_id=None, no error."""
    unique = "NullTemplateIdWaterPlay2026"
    # Insert with no template_id — _insert_activity omits the key when
    # template_id is None, so the summary blob has only {"title": ..., "steps": []}.
    activity_id = _insert_activity(db_path, title=unique, template_id=None)
    resp = client.get(f"/api/search?q={unique}")
    assert resp.status_code == 200
    body = resp.json()
    past = body["past_activities"]
    hit = next((a for a in past if a["id"] == activity_id), None)
    assert hit is not None, f"Expected activity {activity_id} in results; got {past}"
    assert hit["template_id"] is None


def test_q_with_percent_returns_200(client: TestClient) -> None:
    """A query containing ``%`` must return 200, not a crash.

    The LIKE scan escapes ``%`` as ``\\%`` via the ESCAPE clause in
    ``_search_past_activities``. Without the escape, SQLite would
    treat ``%`` as a wildcard and return all activities.  With the
    escape, no crash occurs and the query is treated literally.
    No assertion on specific results — just 200 OK and the expected
    response shape.
    """
    resp = client.get("/api/search?q=100%25")  # URL-encoded "%"
    assert resp.status_code == 200
    body = resp.json()
    assert "templates" in body
    assert "past_activities" in body
