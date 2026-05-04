"""Integration tests for the Step 18 child-profile CRUD API.

Covers the full round-trip (POST → GET → PATCH → DELETE), 404/422/409
error paths, sort order, auth gating, and the regression that deleting
a child does not touch the ``feedback`` table (Step 20's anti-signal
keys on ``signature``, not ``child_id``).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from toybox.db.connection import connect


def _post(
    client: TestClient,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    expect: int = 201,
) -> dict[str, Any]:
    response = client.post("/api/children", json=body, headers=headers)
    assert response.status_code == expect, response.text
    return cast("dict[str, Any]", response.json())


def test_full_crud_round_trip(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    created = _post(
        client,
        parent_headers,
        {
            "display_name": "Alice",
            "birthdate": "2020-03-15",
            "pronouns": "she/her",
            "reading_level": "early-reader",
            "interests": "dinosaurs, drawing",
            "comfort": "stuffed bunny",
            "banned_themes": "spiders, loud noises",
            "notes": "tested via crud round trip",
        },
    )
    assert created["display_name"] == "Alice"
    assert created["birthdate"] == "2020-03-15"
    assert created["reading_level"] == "early-reader"
    assert isinstance(created["id"], str) and len(created["id"]) >= 16
    child_id = created["id"]

    got = client.get(f"/api/children/{child_id}", headers=parent_headers)
    assert got.status_code == 200
    assert got.json() == created

    patched = client.patch(
        f"/api/children/{child_id}",
        json={"display_name": "Alice B.", "interests": "puzzles"},
        headers=parent_headers,
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["display_name"] == "Alice B."
    assert body["interests"] == "puzzles"
    # untouched fields preserved
    assert body["birthdate"] == "2020-03-15"
    assert body["banned_themes"] == "spiders, loud noises"

    deleted = client.delete(f"/api/children/{child_id}", headers=parent_headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}

    gone = client.get(f"/api/children/{child_id}", headers=parent_headers)
    assert gone.status_code == 404
    assert gone.json()["detail"]["code"] == "child_not_found"


def test_post_strips_display_name(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    body = _post(client, parent_headers, {"display_name": "  Bob  "})
    assert body["display_name"] == "Bob"


def test_post_rejects_empty_display_name(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/children",
        json={"display_name": ""},
        headers=parent_headers,
    )
    assert response.status_code == 422


def test_post_rejects_whitespace_display_name(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/children",
        json={"display_name": "   "},
        headers=parent_headers,
    )
    assert response.status_code == 422


def test_post_rejects_overlong_display_name(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/children",
        json={"display_name": "a" * 41},
        headers=parent_headers,
    )
    assert response.status_code == 422


def test_post_accepts_overlong_raw_when_strip_brings_under_cap(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Length cap applies to the *stripped* value, not the raw input.

    Pins the iter-2 deviation: the field-level ``max_length=40`` was
    dropped so the post-strip ``len(stripped) > 40`` validator governs.
    Without that change a 41-char raw value with a leading space would
    422 even though the stored value is 40 chars.
    """
    raw = " " + "a" * 40  # 41 chars raw, 40 after lstrip
    body = _post(client, parent_headers, {"display_name": raw})
    assert body["display_name"] == "a" * 40


def test_post_rejects_bad_reading_level(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/children",
        json={"display_name": "A", "reading_level": "advanced"},
        headers=parent_headers,
    )
    assert response.status_code == 422


def test_post_rejects_bad_birthdate(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    response = client.post(
        "/api/children",
        json={"display_name": "A", "birthdate": "03/15/2020"},
        headers=parent_headers,
    )
    assert response.status_code == 422


def test_get_404_on_unknown_id(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    response = client.get("/api/children/no-such-id", headers=parent_headers)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "child_not_found"


def test_patch_404_on_unknown_id(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    response = client.patch(
        "/api/children/no-such-id",
        json={"display_name": "Z"},
        headers=parent_headers,
    )
    assert response.status_code == 404


def test_delete_404_on_unknown_id(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    response = client.delete("/api/children/no-such-id", headers=parent_headers)
    assert response.status_code == 404


def test_patch_partial_only_updates_provided_fields(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    created = _post(
        client,
        parent_headers,
        {
            "display_name": "Carol",
            "pronouns": "she/her",
            "interests": "trains",
        },
    )
    response = client.patch(
        f"/api/children/{created['id']}",
        json={"interests": "trains, planes"},
        headers=parent_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Carol"
    assert body["pronouns"] == "she/her"
    assert body["interests"] == "trains, planes"


def test_patch_can_clear_optional_field_with_null(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    created = _post(
        client,
        parent_headers,
        {"display_name": "Dee", "pronouns": "they/them"},
    )
    response = client.patch(
        f"/api/children/{created['id']}",
        json={"pronouns": None},
        headers=parent_headers,
    )
    assert response.status_code == 200
    assert response.json()["pronouns"] is None


def test_patch_rejects_empty_display_name(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    created = _post(client, parent_headers, {"display_name": "Eve"})
    response = client.patch(
        f"/api/children/{created['id']}",
        json={"display_name": ""},
        headers=parent_headers,
    )
    assert response.status_code == 422


def test_patch_rejects_bad_reading_level(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    created = _post(client, parent_headers, {"display_name": "Eve"})
    response = client.patch(
        f"/api/children/{created['id']}",
        json={"reading_level": "wizard"},
        headers=parent_headers,
    )
    assert response.status_code == 422


def test_list_sorted_case_insensitive(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    # Insert in non-alphabetical order with mixed case to prove the
    # COLLATE NOCASE clause is doing the work.
    for name in ("Bob", "alice", "charlie"):
        _post(client, parent_headers, {"display_name": name})
    response = client.get("/api/children", headers=parent_headers)
    assert response.status_code == 200
    names = [c["display_name"] for c in response.json()["children"]]
    assert names == ["alice", "Bob", "charlie"]


def test_list_returns_empty_when_no_children(
    client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    response = client.get("/api/children", headers=parent_headers)
    assert response.status_code == 200
    assert response.json() == {"children": []}


def test_delete_409_when_referenced_by_activity(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    created = _post(client, parent_headers, {"display_name": "Frankie"})
    child_id = created["id"]

    # Insert a session + activity row that references this child via the
    # JSON-encoded child_ids column. Two activities so we can verify the
    # referring count is reported accurately.
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                ("sess-1", "2026-05-01T00:00:00Z"),
            )
            for i in range(2):
                conn.execute(
                    "INSERT INTO activities "
                    "(id, session_id, state, version, child_ids, "
                    " intent_source, created_at) "
                    "VALUES (?, ?, 'proposed', 1, ?, 'request_play', ?)",
                    (
                        f"act-{i}",
                        "sess-1",
                        json.dumps([child_id]),
                        "2026-05-01T00:00:00Z",
                    ),
                )
    finally:
        conn.close()

    response = client.delete(f"/api/children/{child_id}", headers=parent_headers)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "child_in_use"
    assert detail["child_id"] == child_id
    assert detail["referring_activity_count"] == 2

    # Profile is still there.
    still = client.get(f"/api/children/{child_id}", headers=parent_headers)
    assert still.status_code == 200


def test_delete_ok_when_only_other_children_referenced(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """LIKE-substring conflict check must not match a *different* child id.

    Defends against a regex/LIKE bug where (e.g.) ``"abc"`` would
    accidentally match an activity referencing ``"abcdef"``.
    """
    target = _post(client, parent_headers, {"display_name": "Gigi"})
    other = _post(client, parent_headers, {"display_name": "Other"})
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                ("sess-2", "2026-05-01T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO activities "
                "(id, session_id, state, version, child_ids, "
                " intent_source, created_at) "
                "VALUES (?, ?, 'proposed', 1, ?, 'request_play', ?)",
                (
                    "act-other",
                    "sess-2",
                    json.dumps([other["id"]]),
                    "2026-05-01T00:00:00Z",
                ),
            )
    finally:
        conn.close()

    response = client.delete(f"/api/children/{target['id']}", headers=parent_headers)
    assert response.status_code == 200, response.text


def test_delete_does_not_touch_feedback_table(
    client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Step 20 regression: feedback rows key on signature, not child_id.

    Deleting a child must not cascade-clear feedback rows. We seed a
    feedback row tied to a session-only activity (no child reference)
    and verify it survives the delete.
    """
    created = _post(client, parent_headers, {"display_name": "Henry"})
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                ("sess-3", "2026-05-01T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO activities "
                "(id, session_id, state, version, child_ids, "
                " intent_source, created_at) "
                "VALUES (?, ?, 'proposed', 1, NULL, 'request_play', ?)",
                ("act-fb", "sess-3", "2026-05-01T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO feedback "
                "(id, activity_id, step_seq, kind, signature, reason, "
                " created_at) VALUES (?, ?, NULL, 'loved_it', ?, NULL, ?)",
                (uuid.uuid4().hex, "act-fb", "sig-xyz", "2026-05-01T00:00:00Z"),
            )
    finally:
        conn.close()

    before = _feedback_rows(db_path)
    assert len(before) == 1

    response = client.delete(f"/api/children/{created['id']}", headers=parent_headers)
    assert response.status_code == 200

    after = _feedback_rows(db_path)
    assert after == before


def _feedback_rows(db_path: Path) -> list[tuple[Any, ...]]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, activity_id, kind, signature, reason FROM feedback ORDER BY id"
        ).fetchall()
        return [tuple(r) for r in rows]
    finally:
        conn.close()


# Endpoint table for the auth-gate tests below. Mirrors the dispatch
# table style used in ``test_auth.py::test_post_pair_requires_parent_auth``.
_PROTECTED_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/children", None),
    ("GET", "/api/children/abc", None),
    ("POST", "/api/children", {"display_name": "X"}),
    ("PATCH", "/api/children/abc", {"display_name": "X"}),
    ("DELETE", "/api/children/abc", None),
]


@pytest.mark.parametrize(("method", "path", "body"), _PROTECTED_ENDPOINTS)
def test_endpoints_require_parent_token(
    client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    """Every CRUD endpoint must 401 without a token.

    Mirrors the convention pinned by ``test_post_pair_requires_parent_auth``
    in ``test_auth.py``.
    """
    response = client.request(method, path, json=body)
    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), _PROTECTED_ENDPOINTS)
def test_child_token_forbidden(
    client: TestClient,
    child_token: str,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    """Child-scope tokens must not have access to the editor endpoints."""
    headers = {"Authorization": f"Bearer {child_token}"}
    response = client.request(method, path, json=body, headers=headers)
    assert response.status_code == 403
