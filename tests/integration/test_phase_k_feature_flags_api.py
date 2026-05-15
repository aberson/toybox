"""Integration coverage for the eight Phase K feature-flag endpoints.

One parametrized test class covers GET / PUT round-trip + auth gating
for every flag (eight endpoints x four assertions = full matrix). Two
representative flags (``jokes_enabled``, ``play_spontaneity_enabled``)
also get an explicit GET-PUT-GET persistence test against the live
SQLite file — the latter exercises the false-default path so the
``play_spontaneity_enabled`` opt-in semantics survive a regression
that would silently flip the wire to default-true.

Mirrors :mod:`tests.integration.test_play_cadence_seconds_api`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.api.auth_dep import get_auth_db
from toybox.api.clickable_words_enabled_settings import (
    get_db as clickable_words_enabled_get_db,
)
from toybox.api.jokes_enabled_settings import get_db as jokes_enabled_get_db
from toybox.api.play_embedded_enabled_settings import (
    get_db as play_embedded_enabled_get_db,
)
from toybox.api.play_endings_enabled_settings import (
    get_db as play_endings_enabled_get_db,
)
from toybox.api.play_spontaneity_enabled_settings import (
    get_db as play_spontaneity_enabled_get_db,
)
from toybox.api.play_standalone_enabled_settings import (
    get_db as play_standalone_enabled_get_db,
)
from toybox.api.read_me_button_enabled_settings import (
    get_db as read_me_button_enabled_get_db,
)
from toybox.api.songs_enabled_settings import get_db as songs_enabled_get_db
from toybox.app import create_app
from toybox.core.auth import TokenScope, issue_token
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations


@dataclass(frozen=True)
class FlagEndpoint:
    """Parameterizes the suite over the eight Phase K flag endpoints.

    ``get_db_dep`` is each module's ``get_db`` FastAPI dependency so
    the per-endpoint override is targeted (mirrors the pattern in
    test_play_cadence_seconds_api.py).
    """

    key: str
    path: str  # ``/api/settings/<kebab-case>``
    default: bool
    get_db_dep: object


FLAG_ENDPOINTS: list[FlagEndpoint] = [
    FlagEndpoint("jokes_enabled", "/api/settings/jokes-enabled", True, jokes_enabled_get_db),
    FlagEndpoint("songs_enabled", "/api/settings/songs-enabled", True, songs_enabled_get_db),
    FlagEndpoint(
        "play_standalone_enabled",
        "/api/settings/play-standalone-enabled",
        True,
        play_standalone_enabled_get_db,
    ),
    FlagEndpoint(
        "play_embedded_enabled",
        "/api/settings/play-embedded-enabled",
        True,
        play_embedded_enabled_get_db,
    ),
    FlagEndpoint(
        "play_endings_enabled",
        "/api/settings/play-endings-enabled",
        True,
        play_endings_enabled_get_db,
    ),
    FlagEndpoint(
        "play_spontaneity_enabled",
        "/api/settings/play-spontaneity-enabled",
        False,
        play_spontaneity_enabled_get_db,
    ),
    FlagEndpoint(
        "clickable_words_enabled",
        "/api/settings/clickable-words-enabled",
        True,
        clickable_words_enabled_get_db,
    ),
    FlagEndpoint(
        "read_me_button_enabled",
        "/api/settings/read-me-button-enabled",
        True,
        read_me_button_enabled_get_db,
    ),
]


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

    # Override every Phase K flag's get_db plus the auth dependency, so
    # all eight endpoints share the test DB. Mirrors
    # test_play_cadence_seconds_api.py's pattern (override get_db +
    # get_auth_db both).
    for endpoint in FLAG_ENDPOINTS:
        app.dependency_overrides[endpoint.get_db_dep] = _override_db  # type: ignore[index]
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


@pytest.mark.parametrize("endpoint", FLAG_ENDPOINTS, ids=[e.key for e in FLAG_ENDPOINTS])
def test_get_returns_seeded_default(client: TestClient, endpoint: FlagEndpoint) -> None:
    """Fresh migrated DB → GET returns the spec'd default per flag.

    Wire-shape assertion: byte-identical ``{"value": <bool>}`` for all
    eight. A regression that wrapped the value (e.g. nested under
    ``{"enabled": ...}``) would surface here on every flag.
    """
    response = client.get(endpoint.path)
    assert response.status_code == 200
    assert response.json() == {"value": endpoint.default}


@pytest.mark.parametrize("endpoint", FLAG_ENDPOINTS, ids=[e.key for e in FLAG_ENDPOINTS])
def test_put_toggles_value_and_round_trips(
    client: TestClient,
    parent_headers: dict[str, str],
    endpoint: FlagEndpoint,
) -> None:
    """PUT inverts the default, echoes the new value, GET reads it back."""
    inverted = not endpoint.default
    put_response = client.put(endpoint.path, json={"value": inverted}, headers=parent_headers)
    assert put_response.status_code == 200
    assert put_response.json() == {"value": inverted}

    get_response = client.get(endpoint.path)
    assert get_response.status_code == 200
    assert get_response.json() == {"value": inverted}


@pytest.mark.parametrize("endpoint", FLAG_ENDPOINTS, ids=[e.key for e in FLAG_ENDPOINTS])
def test_put_without_token_returns_401(client: TestClient, endpoint: FlagEndpoint) -> None:
    """No bearer token → 401 (RequireScope default for missing creds)."""
    response = client.put(endpoint.path, json={"value": False})
    assert response.status_code == 401


@pytest.mark.parametrize("endpoint", FLAG_ENDPOINTS, ids=[e.key for e in FLAG_ENDPOINTS])
def test_put_with_child_token_returns_403(
    client: TestClient,
    child_headers: dict[str, str],
    endpoint: FlagEndpoint,
) -> None:
    """Child-scope token cannot change a household setting → 403."""
    response = client.put(endpoint.path, json={"value": False}, headers=child_headers)
    assert response.status_code == 403


def test_put_non_bool_value_returns_422(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Object body → 422 (FastAPI / Pydantic validation).

    Tested against ONE representative endpoint (``jokes-enabled``) —
    all eight share the identical Pydantic ``{value: bool}`` request
    model so the validation behavior is structurally identical.
    Re-running the same 422 assertion 8× was iter-1 spam (M3 from the
    review): it re-tests FastAPI/Pydantic, not toybox code.

    Pydantic v2 coerces a handful of bool-ish strings (``"true"``,
    ``"yes"``, ``"on"``, ``"1"``) and numerics — that coercion is
    intentional and inherited from every other settings endpoint that
    accepts a bool. To exercise the validation path here we send a
    JSON object which has no bool coercion. The helper's isinstance
    guard guarantees that once Pydantic accepts a value it really is
    a Python bool, so a string slipping through here can't end up
    persisted as anything weird in SQLite.
    """
    response = client.put(
        "/api/settings/jokes-enabled",
        json={"value": {"not": "a bool"}},
        headers=parent_headers,
    )
    assert response.status_code == 422


# --- Representative deep round-trip tests -----------------------------
# Two endpoints get an explicit "the value persists to disk across a
# fresh DB connection" test. ``jokes_enabled`` exercises the default-
# true branch; ``play_spontaneity_enabled`` exercises the
# default-false branch — the only opt-in flag of the eight.


def test_jokes_enabled_persists_to_disk(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Round-trip a PUT through the API and read the raw SQLite row.

    Catches a hypothetical "the endpoint returns OK but the helper
    never wrote" regression. Mirrors the integration-test depth
    Phase J's banned-themes test uses on the wire shape (code-quality
    §3 — audit wire shape when storage representation changes).
    """
    put_response = client.put(
        "/api/settings/jokes-enabled",
        json={"value": False},
        headers=parent_headers,
    )
    assert put_response.status_code == 200
    assert put_response.json() == {"value": False}

    # Open a fresh sqlite connection and read the raw stored string —
    # this proves the value made it past the helper down to disk.
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", ("jokes_enabled",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    assert raw == "false"


def test_play_spontaneity_enabled_persists_to_disk(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Same shape as ``test_jokes_enabled_persists_to_disk`` but for the opt-in flag.

    Toggles the spontaneity flag ON and verifies the raw stored value
    flipped to ``'true'``. The default of this flag is ``False`` —
    Phase K's only opt-in — so this test also pins the migration seed
    against silent drift.
    """
    # Confirm starting state via GET first.
    initial_response = client.get("/api/settings/play-spontaneity-enabled")
    assert initial_response.status_code == 200
    assert initial_response.json() == {"value": False}

    put_response = client.put(
        "/api/settings/play-spontaneity-enabled",
        json={"value": True},
        headers=parent_headers,
    )
    assert put_response.status_code == 200
    assert put_response.json() == {"value": True}

    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            ("play_spontaneity_enabled",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    assert raw == "true"
