"""Integration tests for GET /api/catalog.

Phase T Step T2: full template catalog endpoint, exercised through the
production FastAPI app via TestClient.

Coverage:
1. GET /api/catalog returns 200 with valid JSON.
2. All entries have non-empty ``id`` and an ``intent`` in ``SUPPORTED_INTENTS``.
3. Spot-check: a well-known template id (``boredom_morning_explore``) appears
   in the catalog and is associated with the ``boredom`` intent.
4. No ``Authorization`` header is required (public endpoint).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from toybox.activities.generator import SUPPORTED_INTENTS
from toybox.app import create_app

# A stable template id that exists in the shipped boredom corpus.
KNOWN_TEMPLATE_ID = "boredom_morning_explore"
KNOWN_TEMPLATE_INTENT = "boredom"


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as tc:
        yield tc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_catalog_200_valid_json(client: TestClient) -> None:
    """GET /api/catalog returns 200 and parses as JSON with ``entries`` key."""
    resp = client.get("/api/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert "entries" in body
    assert isinstance(body["entries"], list)


def test_all_entries_have_nonempty_id(client: TestClient) -> None:
    """Every catalog entry carries a non-empty string ``id``."""
    body = client.get("/api/catalog").json()
    for entry in body["entries"]:
        assert entry.get("id"), f"Entry has empty or missing id: {entry}"


def test_all_entries_have_valid_intent(client: TestClient) -> None:
    """Every catalog entry's ``intent`` is one of the four supported intents."""
    valid = set(SUPPORTED_INTENTS)
    body = client.get("/api/catalog").json()
    bad = [e for e in body["entries"] if e.get("intent") not in valid]
    assert bad == [], f"Entries with unknown intent: {bad}"


def test_known_template_id_present(client: TestClient) -> None:
    """The well-known template ``boredom_morning_explore`` appears in the catalog."""
    body = client.get("/api/catalog").json()
    entries = {e["id"]: e for e in body["entries"]}
    assert KNOWN_TEMPLATE_ID in entries, (
        f"Expected {KNOWN_TEMPLATE_ID!r} in catalog ids; "
        f"got {sorted(entries)[:10]} ..."
    )
    entry = entries[KNOWN_TEMPLATE_ID]
    assert entry["intent"] == KNOWN_TEMPLATE_INTENT, (
        f"Expected intent {KNOWN_TEMPLATE_INTENT!r}, got {entry['intent']!r}"
    )


def test_no_auth_required(client: TestClient) -> None:
    """No Authorization header is needed — catalog is a public endpoint."""
    resp = client.get("/api/catalog")
    assert resp.status_code == 200
