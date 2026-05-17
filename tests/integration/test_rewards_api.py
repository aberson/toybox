"""Integration tests for the Phase L Step L2 rewards CRUD API.

Boots the full FastAPI app via the conftest fixture and exercises
upload → confirm → list/get/patch/delete end-to-end. Mirrors the
shape of :mod:`tests.integration.test_toys_api` but tailored to the
rewards-specific contracts (slug-derived ids, JSON-encoded tags,
animation enum, no vision call).

PATCH and DELETE intentionally skip an ``If-Match-Version`` header
check because migration 0019 ships no ``version`` column on
``rewards``. Other resources (activities, children) carry a version
column and use ``If-Match-Version`` for optimistic concurrency; L2's
rewards table doesn't. If a follow-up phase adds versioning, the
existing test scaffold here is the place to require the header.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from toybox.api import rewards as rewards_router_mod

# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``data/`` writes to a fresh temp dir per test."""
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def reset_staging_registry() -> Iterator[None]:
    """Each test starts with an empty staging registry."""
    rewards_router_mod._staging_extensions.clear()
    yield
    rewards_router_mod._staging_extensions.clear()


@pytest.fixture
def rc_client(app: FastAPI) -> Iterator[TestClient]:
    """``TestClient`` bound to the rewards-enabled app."""
    with TestClient(app) as test_client:
        yield test_client


def _png_bytes(
    size: tuple[int, int] = (64, 64),
    color: tuple[int, int, int] = (50, 100, 200),
) -> bytes:
    img = Image.new("RGB", size, color)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _upload_files(
    payload: bytes,
    filename: str = "reward.png",
    mime: str = "image/png",
) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (filename, payload, mime)}


def _upload_and_confirm(
    client: TestClient,
    parent_headers: dict[str, str],
    *,
    display_name: str,
    tags: list[str],
    animation: str = "shine",
    active: bool = True,
    color: tuple[int, int, int] = (50, 100, 200),
) -> dict[str, Any]:
    """Helper: upload a unique PNG then confirm with the given fields.

    Returns the parsed RewardResponse dict from the confirm call.
    """
    upload_resp = client.post(
        "/api/rewards/upload",
        files=_upload_files(_png_bytes(color=color)),
        headers=parent_headers,
    )
    assert upload_resp.status_code == 200, upload_resp.text
    staging_key = upload_resp.json()["staging_key"]
    confirm_resp = client.post(
        "/api/rewards",
        json={
            "staging_key": staging_key,
            "display_name": display_name,
            "tags": tags,
            "animation": animation,
            "active": active,
        },
        headers=parent_headers,
    )
    assert confirm_resp.status_code == 201, confirm_resp.text
    return confirm_resp.json()


# ---------------------------------------------------------------------
# Auth gating: parent-scope required on EVERY rewards endpoint (GET
# included). Matches the existing ``toys.py`` / ``rooms.py`` convention;
# the plan §8 table mistakenly listed GET as no-auth — the codebase
# pattern wins (code-quality.md §1).
# ---------------------------------------------------------------------


_PARENT_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/rewards/upload", None),
    (
        "POST",
        "/api/rewards",
        {
            "staging_key": "x",
            "display_name": "X",
            "tags": [],
            "animation": "shine",
        },
    ),
    ("GET", "/api/rewards", None),
    ("GET", "/api/rewards/some-id", None),
    ("PATCH", "/api/rewards/some-id", {"display_name": "Y"}),
    ("DELETE", "/api/rewards/some-id", None),
]


@pytest.mark.parametrize(("method", "path", "body"), _PARENT_ENDPOINTS)
def test_parent_endpoints_require_token(
    rc_client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    """Every rewards endpoint must 401 without a token."""
    if method == "POST" and path.endswith("/upload"):
        response = rc_client.post(path, files=_upload_files(b"x"))
    elif method == "GET":
        response = rc_client.get(path)
    else:
        response = rc_client.request(method, path, json=body)
    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), _PARENT_ENDPOINTS)
def test_parent_endpoints_reject_child_token(
    rc_client: TestClient,
    child_token: str,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    """Child-scope tokens must not have access to any rewards endpoint."""
    headers = {"Authorization": f"Bearer {child_token}"}
    if method == "POST" and path.endswith("/upload"):
        response = rc_client.post(path, files=_upload_files(b"x"), headers=headers)
    elif method == "GET":
        response = rc_client.get(path, headers=headers)
    else:
        response = rc_client.request(method, path, json=body, headers=headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------
# Upload endpoint
# ---------------------------------------------------------------------


def test_upload_returns_staging_key_hash_and_dimensions(
    rc_client: TestClient, parent_headers: dict[str, str]
) -> None:
    """POST /api/rewards/upload — happy path: PNG → staging key + dims."""
    resp = rc_client.post(
        "/api/rewards/upload",
        files=_upload_files(_png_bytes()),
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["staging_key"], str) and body["staging_key"]
    assert isinstance(body["image_hash"], str) and len(body["image_hash"]) == 64
    assert body["mime_type"] == "image/png"
    assert body["width"] == 64
    assert body["height"] == 64


def test_upload_bad_mime_rejected(rc_client: TestClient, parent_headers: dict[str, str]) -> None:
    """Plain text uploaded as image/png is sniffed + rejected (415)."""
    resp = rc_client.post(
        "/api/rewards/upload",
        files=_upload_files(b"hello world\n", filename="fake.png", mime="image/png"),
        headers=parent_headers,
    )
    assert resp.status_code == 415
    assert resp.json()["detail"]["code"] == "upload_bad_mime"


def test_upload_409_on_active_duplicate_hash(
    rc_client: TestClient, parent_headers: dict[str, str]
) -> None:
    """A second upload of identical bytes (still active) returns 409."""
    payload = _png_bytes(color=(7, 11, 13))
    first = rc_client.post(
        "/api/rewards/upload",
        files=_upload_files(payload),
        headers=parent_headers,
    )
    assert first.status_code == 200
    # Confirm it so it lands in the rewards table as active.
    staging_key = first.json()["staging_key"]
    confirm = rc_client.post(
        "/api/rewards",
        json={
            "staging_key": staging_key,
            "display_name": "Original Reward",
            "tags": [],
            "animation": "shine",
        },
        headers=parent_headers,
    )
    assert confirm.status_code == 201
    # Re-uploading the same bytes now 409s against the active row.
    repeat = rc_client.post(
        "/api/rewards/upload",
        files=_upload_files(payload),
        headers=parent_headers,
    )
    assert repeat.status_code == 409
    detail = repeat.json()["detail"]
    assert detail["code"] == "image_already_exists"
    assert detail["existing_reward"]["display_name"] == "Original Reward"


# ---------------------------------------------------------------------
# Confirm endpoint
# ---------------------------------------------------------------------


def test_confirm_inserts_row_and_returns_shape(
    rc_client: TestClient,
    parent_headers: dict[str, str],
    isolated_data_root: Path,
) -> None:
    """Full confirm: row persisted, file moved, response shape correct.

    Pins the committed filename equals ``<slug>.<ext>`` (NOT the staging
    UUID) per invariant 8 — the post-derive_slug rename step renames
    ``<uuid>.png`` to ``treasure-chest.png`` before the DB insert.
    """
    body = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name="Treasure Chest",
        tags=["pirate", "gold"],
        animation="shine",
    )
    # Response shape — every RewardResponse field present + typed.
    assert body["id"] == "treasure-chest"
    assert body["display_name"] == "Treasure Chest"
    assert body["image_path"] == "data/images/rewards/treasure-chest.png"
    assert len(body["image_hash"]) == 64
    assert body["tags"] == ["pirate", "gold"]
    assert body["animation"] == "shine"
    assert body["active"] is True
    assert body["archived"] is False
    assert body["created_at"].endswith("Z")
    assert body["last_used_at"] is None
    # File landed on disk under the rewards subdir with the slug-named
    # filename (NOT a staging UUID).
    on_disk = isolated_data_root / "images" / "rewards" / "treasure-chest.png"
    assert on_disk.is_file()
    # And no UUID-named leftover.
    siblings = list((isolated_data_root / "images" / "rewards").iterdir())
    assert [p.name for p in siblings] == ["treasure-chest.png"]


def test_confirm_404_for_unknown_staging_key(
    rc_client: TestClient, parent_headers: dict[str, str]
) -> None:
    """A confirm with a never-staged key 404s with staging_not_found."""
    resp = rc_client.post(
        "/api/rewards",
        json={
            "staging_key": "nonexistent-key",
            "display_name": "X",
            "tags": [],
            "animation": "shine",
        },
        headers=parent_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "staging_not_found"


# ---------------------------------------------------------------------
# Tag normalization + validation
# ---------------------------------------------------------------------


def test_tag_normalization_strip_lower_dedupe_dropempty(
    rc_client: TestClient, parent_headers: dict[str, str]
) -> None:
    """Plan-spec normalization: input variants → single ``"pirate"`` tag."""
    body = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name="Variant Reward",
        tags=["Pirate", " pirate", "PIRATE  ", ""],
        animation="jump",
    )
    assert body["tags"] == ["pirate"]


def test_tags_too_many_returns_422(rc_client: TestClient, parent_headers: dict[str, str]) -> None:
    """11 distinct normalized tags → 422 (cap is 10)."""
    upload = rc_client.post(
        "/api/rewards/upload",
        files=_upload_files(_png_bytes(color=(1, 2, 3))),
        headers=parent_headers,
    )
    assert upload.status_code == 200
    staging_key = upload.json()["staging_key"]
    resp = rc_client.post(
        "/api/rewards",
        json={
            "staging_key": staging_key,
            "display_name": "Too Many Tags",
            "tags": [f"tag{i}" for i in range(11)],
            "animation": "shine",
        },
        headers=parent_headers,
    )
    assert resp.status_code == 422


def test_tag_too_long_returns_422(rc_client: TestClient, parent_headers: dict[str, str]) -> None:
    """A 25-char tag → 422 (per-tag cap is 24)."""
    upload = rc_client.post(
        "/api/rewards/upload",
        files=_upload_files(_png_bytes(color=(2, 3, 4))),
        headers=parent_headers,
    )
    assert upload.status_code == 200
    staging_key = upload.json()["staging_key"]
    resp = rc_client.post(
        "/api/rewards",
        json={
            "staging_key": staging_key,
            "display_name": "Long Tag",
            "tags": ["x" * 25],
            "animation": "shine",
        },
        headers=parent_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------
# GET (list + by-id)
# ---------------------------------------------------------------------


def test_list_returns_active_first_by_recency(
    rc_client: TestClient, parent_headers: dict[str, str], db_path: Path
) -> None:
    """List sort: active=1 partition before active=0; recent first."""
    from toybox.db.connection import connect

    older = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name="Older Reward",
        tags=[],
        animation="shine",
        color=(10, 10, 10),
    )
    newer = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name="Newer Reward",
        tags=[],
        animation="jump",
        color=(20, 20, 20),
    )
    inactive = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name="Inactive Reward",
        tags=[],
        animation="spin",
        active=False,
        color=(30, 30, 30),
    )
    # Pin last_used_at directly so the recency sort is deterministic
    # (the API doesn't have an endpoint to write it — L3 will, but L2
    # just exposes the column read-only on responses).
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE rewards SET last_used_at = ? WHERE id = ?",
                ("2026-05-15T10:00:00Z", older["id"]),
            )
            conn.execute(
                "UPDATE rewards SET last_used_at = ? WHERE id = ?",
                ("2026-05-16T10:00:00Z", newer["id"]),
            )
    finally:
        conn.close()
    resp = rc_client.get("/api/rewards", headers=parent_headers)
    assert resp.status_code == 200
    rows = resp.json()["rewards"]
    ids = [r["id"] for r in rows]
    # active=1 partition (newer + older) appears before active=0 (inactive).
    assert ids.index(newer["id"]) < ids.index(inactive["id"])
    assert ids.index(older["id"]) < ids.index(inactive["id"])
    # Within active partition, newer last_used_at comes first.
    assert ids.index(newer["id"]) < ids.index(older["id"])


def test_list_hides_archived_by_default(
    rc_client: TestClient, parent_headers: dict[str, str]
) -> None:
    """Archived rewards do not appear in list output (no toggle param)."""
    visible = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name="Visible",
        tags=[],
        animation="shine",
        color=(11, 22, 33),
    )
    hidden = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name="Hidden",
        tags=[],
        animation="spin",
        color=(44, 55, 66),
    )
    del_resp = rc_client.delete(f"/api/rewards/{hidden['id']}", headers=parent_headers)
    assert del_resp.status_code == 200
    list_resp = rc_client.get("/api/rewards", headers=parent_headers)
    assert list_resp.status_code == 200
    ids = [r["id"] for r in list_resp.json()["rewards"]]
    assert visible["id"] in ids
    assert hidden["id"] not in ids


def test_get_404_for_unknown_id(rc_client: TestClient, parent_headers: dict[str, str]) -> None:
    """GET /api/rewards/{missing} → 404 reward_not_found."""
    resp = rc_client.get("/api/rewards/no-such-reward", headers=parent_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "reward_not_found"


def test_get_returns_archived_by_id(rc_client: TestClient, parent_headers: dict[str, str]) -> None:
    """Archived rewards remain reachable by id (list hides; get shows)."""
    created = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name="Archive Me",
        tags=[],
        animation="wobble",
    )
    rc_client.delete(f"/api/rewards/{created['id']}", headers=parent_headers)
    resp = rc_client.get(f"/api/rewards/{created['id']}", headers=parent_headers)
    assert resp.status_code == 200
    assert resp.json()["archived"] is True


# ---------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------


def test_patch_updates_all_fields(rc_client: TestClient, parent_headers: dict[str, str]) -> None:
    """PATCH updates display_name, tags (re-normalizes), animation, active."""
    created = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name="Original Name",
        tags=["initial"],
        animation="shine",
    )
    patch_resp = rc_client.patch(
        f"/api/rewards/{created['id']}",
        json={
            "display_name": "Updated Name",
            "tags": ["NEW", " new", "Different"],  # normalize + dedupe
            "animation": "spin",
            "active": False,
        },
        headers=parent_headers,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()
    assert body["display_name"] == "Updated Name"
    assert body["tags"] == ["new", "different"]
    assert body["animation"] == "spin"
    assert body["active"] is False
    # id is immutable — the slug doesn't change just because display_name does.
    assert body["id"] == created["id"]


def test_patch_archived_toggle(rc_client: TestClient, parent_headers: dict[str, str]) -> None:
    """PATCH archived=true mirrors DELETE behavior (set archived=1)."""
    created = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name="Archive Via Patch",
        tags=[],
        animation="float",
    )
    resp = rc_client.patch(
        f"/api/rewards/{created['id']}",
        json={"archived": True},
        headers=parent_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["archived"] is True


def test_patch_only_one_field_does_not_blank_others(
    rc_client: TestClient, parent_headers: dict[str, str]
) -> None:
    """PATCH with one field must leave every other field at its prior value.

    Highest-value coverage gap from the test-quality reviewer: confirms
    ``exclude_unset=True`` + the explicit-null guards prevent the
    classic "PATCH blanks the fields you didn't touch" defect.
    """
    created = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name="Untouched Fields",
        tags=["alpha", "beta"],
        animation="spin",
        active=True,
    )
    # PATCH only display_name; every other field stays at its prior value.
    resp = rc_client.patch(
        f"/api/rewards/{created['id']}",
        json={"display_name": "Renamed"},
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["display_name"] == "Renamed"
    assert body["tags"] == ["alpha", "beta"]
    assert body["animation"] == "spin"
    assert body["active"] is True
    assert body["archived"] is False


@pytest.mark.parametrize(
    "field",
    ["display_name", "tags", "animation", "active", "archived"],
)
def test_patch_rejects_explicit_null_on_field(
    rc_client: TestClient, parent_headers: dict[str, str], field: str
) -> None:
    """PATCH with an explicit-null body field → 422 (NOT 500, NOT silent corruption).

    Pydantic's ``exclude_unset=True`` only filters fields the client
    never sent — fields explicitly set to ``null`` still reach the
    update loop. Without the ``mode="before"`` guards added in L2
    iter-2, ``{"animation": null}`` 500s, ``{"tags": null}`` writes the
    literal string ``"null"``, and ``{"active": null}`` /
    ``{"archived": null}`` silently flip the bit to 0. Every explicit
    null on a field must return a 422 with a clear error.
    """
    created = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name=f"Patch Null {field}",
        tags=["keep"],
        animation="shine",
        color=(field.__hash__() % 200, 100, 100),
    )
    resp = rc_client.patch(
        f"/api/rewards/{created['id']}",
        json={field: None},
        headers=parent_headers,
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------
# DELETE (soft archive)
# ---------------------------------------------------------------------


def test_delete_soft_archives_returns_archived_row(
    rc_client: TestClient, parent_headers: dict[str, str]
) -> None:
    """DELETE sets archived=1 + returns the row with archived=true."""
    created = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name="Soft Delete Me",
        tags=[],
        animation="pulse",
    )
    del_resp = rc_client.delete(f"/api/rewards/{created['id']}", headers=parent_headers)
    assert del_resp.status_code == 200, del_resp.text
    body = del_resp.json()
    assert body["id"] == created["id"]
    assert body["archived"] is True
    # GET by id still reachable (archived rows visible by id).
    get_resp = rc_client.get(f"/api/rewards/{created['id']}", headers=parent_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["archived"] is True


def test_delete_404_for_missing(rc_client: TestClient, parent_headers: dict[str, str]) -> None:
    """DELETE on a missing id → 404."""
    resp = rc_client.delete("/api/rewards/no-such-reward", headers=parent_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "reward_not_found"


# ---------------------------------------------------------------------
# Slug derivation + collision handling
# ---------------------------------------------------------------------


# NOTE: a stand-alone ``test_slug_derived_from_display_name`` is omitted
# as tautological — ``test_confirm_inserts_row_and_returns_shape`` pins
# the slug-derived id end-to-end and ``tests/unit/test_slugs.py``
# covers the slugifier itself.


def test_slug_collision_suffixes(rc_client: TestClient, parent_headers: dict[str, str]) -> None:
    """Two rewards with the same display_name → second gets ``-2`` suffix."""
    first = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name="Duplicate Name",
        tags=[],
        animation="jump",
        color=(1, 2, 3),
    )
    second = _upload_and_confirm(
        rc_client,
        parent_headers,
        display_name="Duplicate Name",
        tags=[],
        animation="spin",
        color=(4, 5, 6),
    )
    assert first["id"] == "duplicate-name"
    assert second["id"] == "duplicate-name-2"


def test_slug_rejects_all_symbol_display_name(
    rc_client: TestClient, parent_headers: dict[str, str]
) -> None:
    """``display_name="@@@"`` slugifies to "" → 422 invalid_display_name."""
    upload = rc_client.post(
        "/api/rewards/upload",
        files=_upload_files(_png_bytes(color=(99, 99, 99))),
        headers=parent_headers,
    )
    assert upload.status_code == 200
    staging_key = upload.json()["staging_key"]
    resp = rc_client.post(
        "/api/rewards",
        json={
            "staging_key": staging_key,
            "display_name": "@@@",
            "tags": [],
            "animation": "shine",
        },
        headers=parent_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_display_name"


# ---------------------------------------------------------------------
# Animation enum gate (sanity check that bad values 422)
# ---------------------------------------------------------------------


def test_invalid_animation_returns_422(
    rc_client: TestClient, parent_headers: dict[str, str]
) -> None:
    """Animation not in the six-member enum → 422."""
    upload = rc_client.post(
        "/api/rewards/upload",
        files=_upload_files(_png_bytes(color=(77, 77, 77))),
        headers=parent_headers,
    )
    assert upload.status_code == 200
    staging_key = upload.json()["staging_key"]
    resp = rc_client.post(
        "/api/rewards",
        json={
            "staging_key": staging_key,
            "display_name": "Bad Anim",
            "tags": [],
            "animation": "explode",
        },
        headers=parent_headers,
    )
    assert resp.status_code == 422
