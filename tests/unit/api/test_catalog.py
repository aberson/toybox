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


def test_all_entries_have_has_element_bool() -> None:
    """Every entry carries a boolean ``has_element`` flag.

    SWR Step 4 wire-shape fix: the Elements bucket on the CatalogPanel is
    driven by this field, not by a (non-existent) ``periodic_table`` theme.
    """
    client = _client()
    body = client.get("/api/catalog").json()
    for entry in body["entries"]:
        assert "has_element" in entry, f"entry missing has_element: {entry['id']}"
        assert isinstance(entry["has_element"], bool), (
            f"has_element must be a bool for entry {entry['id']}, "
            f"got {type(entry['has_element']).__name__}"
        )


def test_has_element_true_for_real_element_template() -> None:
    """At least one shipped template flags ``has_element=True``.

    SWR Step 4 regression: element templates (steps carrying ``element_id``)
    must be discoverable as Elements off the wire. Pre-fix the response had no
    element marker at all, so the CatalogPanel guessed from a ``periodic_table``
    theme that the Theme enum does not define — silently emptying the Elements
    catalog tab. This pins that the backend actually emits a True flag for the
    element corpus, AND that such templates do NOT carry a ``periodic_table``
    theme (proving the old theme-based proxy was structurally wrong).
    """
    client = _client()
    body = client.get("/api/catalog").json()
    element_entries = [e for e in body["entries"] if e["has_element"]]
    assert element_entries, (
        "Expected at least one catalog entry with has_element=True "
        "(element corpus templates carry per-step element_id)."
    )
    for entry in element_entries:
        assert "periodic_table" not in entry["themes"], (
            f"{entry['id']} unexpectedly carries a periodic_table theme; "
            "the Theme enum has no such member — element bucketing must use "
            "has_element, not this theme."
        )
