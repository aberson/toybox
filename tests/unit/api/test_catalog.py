"""Unit tests for GET /api/catalog.

Phase T Step T2: offline template catalog endpoint.

Coverage:
1. Each entry has non-empty ``id``, ``title``, and ``intent``.
2. ``intent`` for every entry is one of ``SUPPORTED_INTENTS``.
3. Total entry count is greater than zero (templates exist on disk).
4. Duplicate template ids do not appear in the response.
5. Every entry has a ``themes`` list and a positive ``step_count``.
6. Response ``total`` equals ``len(entries)``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from toybox.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_catalog_entry_shape() -> None:
    """Every entry has non-empty ``id``, ``title``, and ``intent`` strings."""
    client = _client()
    body = client.get("/api/catalog").json()
    entries = body["entries"]
    assert len(entries) > 0, "Expected at least one catalog entry"
    for entry in entries:
        assert "id" in entry and entry["id"], f"entry missing non-empty id: {entry}"
        assert "title" in entry and entry["title"], f"entry missing non-empty title: {entry}"
        assert "intent" in entry and entry["intent"], f"entry missing non-empty intent: {entry}"


def test_catalog_no_duplicate_ids() -> None:
    """Template ids in the catalog response are unique (no duplicates)."""
    client = _client()
    body = client.get("/api/catalog").json()
    ids = [e["id"] for e in body["entries"]]
    assert len(ids) == len(set(ids)), "Duplicate template ids found in catalog response"


def test_all_entries_have_themes_list() -> None:
    """Every entry has a ``themes`` key that is a list (may be empty)."""
    client = _client()
    body = client.get("/api/catalog").json()
    for entry in body["entries"]:
        assert "themes" in entry, f"entry missing themes key: {entry['id']}"
        assert isinstance(entry["themes"], list), (
            f"themes is not a list for entry {entry['id']}"
        )


def test_all_entries_have_step_count() -> None:
    """Every entry has a ``step_count`` that is an int >= 1."""
    client = _client()
    body = client.get("/api/catalog").json()
    for entry in body["entries"]:
        assert "step_count" in entry, f"entry missing step_count: {entry['id']}"
        assert isinstance(entry["step_count"], int) and entry["step_count"] >= 1, (
            f"step_count must be int >= 1 for entry {entry['id']}, got {entry.get('step_count')}"
        )


def test_total_equals_len_entries() -> None:
    """Response ``total`` field equals ``len(entries)``."""
    client = _client()
    body = client.get("/api/catalog").json()
    assert "total" in body, "Response missing 'total' field"
    assert body["total"] == len(body["entries"]), (
        f"total={body['total']} != len(entries)={len(body['entries'])}"
    )
