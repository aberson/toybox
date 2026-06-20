"""Integration tests for the Step 17 room bulk-ingest pipeline.

These boot the full FastAPI app via the conftest fixture, mock the
Claude vision client to a deterministic stub, and exercise the
upload-bulk → confirm-bulk → list/patch/delete flow end-to-end.
"""

from __future__ import annotations

import asyncio
import io
import os
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from toybox.ai import capability as capability_mod
from toybox.ai.client import AIResponse, StubClient
from toybox.api import rooms as rooms_router_mod
from toybox.api.rooms import get_vision_client
from toybox.db.connection import connect

# ---------------------------------------------------------------------
# Fixtures: deterministic vision + isolated data root + helpers
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def reset_bulk_staging_registry() -> Iterator[None]:
    rooms_router_mod._bulk_staging_extensions.clear()
    yield
    rooms_router_mod._bulk_staging_extensions.clear()


@pytest.fixture(autouse=True)
def stub_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``is_capable`` to return True so vision runs by default."""

    async def _capable(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        return (True, None)

    monkeypatch.setattr(capability_mod, "is_capable", _capable)
    monkeypatch.setattr(rooms_router_mod, "is_capable", _capable)


@pytest.fixture
def stub_vision_client() -> StubClient:
    """A StubClient that returns a generic Living Room suggestion.

    Tests that need per-photo customisation reset the underlying
    ``_image_responses`` list directly.
    """
    return StubClient(
        image_responses=[
            '{"suggested_room_label": "Living Room", '
            '"features": [{"name": "couch"}, {"name": "rug"}]}'
        ]
        * 100
    )


@pytest.fixture
def app_with_vision(app: FastAPI, stub_vision_client: StubClient) -> FastAPI:
    """The conftest ``app`` fixture, plus a stubbed vision client."""
    app.dependency_overrides[get_vision_client] = lambda: stub_vision_client
    return app


@pytest.fixture
def vc_client(app_with_vision: FastAPI) -> Iterator[TestClient]:
    with TestClient(app_with_vision) as test_client:
        yield test_client


def _jpeg_bytes(
    size: tuple[int, int] = (64, 64),
    color: tuple[int, int, int] = (200, 100, 50),
) -> bytes:
    img = Image.new("RGB", size, color)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


def _bulk_files(
    payloads: list[tuple[bytes, str, str]],
) -> list[tuple[str, tuple[str, bytes, str]]]:
    """Build the list-of-tuples form ``TestClient.post(files=...)`` accepts.

    Each entry is ``("files", (filename, bytes, mime))``. The repeated
    field name is what FastAPI's ``list[UploadFile]`` parameter expects.
    """
    return [("files", (filename, payload, mime)) for payload, filename, mime in payloads]


# ---------------------------------------------------------------------
# Auth gating: every endpoint must reject anonymous + child-scope tokens
# ---------------------------------------------------------------------


_PROTECTED_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/rooms/upload-bulk", None),
    ("POST", "/api/rooms/confirm-bulk", {"batch_id": "x", "assignments": []}),
    ("GET", "/api/rooms", None),
    ("GET", "/api/rooms/abc", None),
    ("GET", "/api/rooms/abc/features", None),
    ("PATCH", "/api/rooms/abc", {"display_name": "Y"}),
    ("DELETE", "/api/rooms/abc", None),
]


@pytest.mark.parametrize(("method", "path", "body"), _PROTECTED_ENDPOINTS)
def test_endpoints_require_parent_token(
    vc_client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    if method == "POST" and path.endswith("/upload-bulk"):
        response = vc_client.post(path, files=_bulk_files([(b"x", "x.jpg", "image/jpeg")]))
    else:
        response = vc_client.request(method, path, json=body)
    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), _PROTECTED_ENDPOINTS)
def test_child_token_forbidden(
    vc_client: TestClient,
    child_token: str,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    headers = {"Authorization": f"Bearer {child_token}"}
    if method == "POST" and path.endswith("/upload-bulk"):
        response = vc_client.post(
            path,
            files=_bulk_files([(b"x", "x.jpg", "image/jpeg")]),
            headers=headers,
        )
    else:
        response = vc_client.request(method, path, json=body, headers=headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------
# Bulk upload happy path + caps + per-file errors
# ---------------------------------------------------------------------


def test_bulk_upload_50_files_happy_path(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    stub_vision_client: StubClient,
) -> None:
    """50 unique photos → 200 with batch_id + 50 photo entries with suggestions."""
    payloads: list[tuple[bytes, str, str]] = []
    for i in range(50):
        # Use a 3-channel spread so JPEG quantisation can't collapse two
        # photos to the same bytes (which DOES happen with single-axis
        # variation — JPEG buckets neighbouring colours).
        payloads.append(
            (
                _jpeg_bytes(color=(i * 5 % 256, (i * 7) % 256, (i * 11) % 256)),
                f"r{i}.jpg",
                "image/jpeg",
            )
        )

    resp = vc_client.post(
        "/api/rooms/upload-bulk",
        files=_bulk_files(payloads),
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["batch_id"], str)
    assert len(body["photos"]) == 50
    suggested = [p for p in body["photos"] if p["suggested"] is not None]
    assert len(suggested) == 50
    assert body["vision_skipped"] is False


def test_bulk_upload_51_files_413(
    vc_client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    payloads: list[tuple[bytes, str, str]] = [
        (
            _jpeg_bytes(color=(i * 5 % 256, (i * 7) % 256, (i * 11) % 256)),
            f"r{i}.jpg",
            "image/jpeg",
        )
        for i in range(51)
    ]
    resp = vc_client.post(
        "/api/rooms/upload-bulk",
        files=_bulk_files(payloads),
        headers=parent_headers,
    )
    assert resp.status_code == 413
    assert resp.json()["detail"]["code"] == "bulk_cap_exceeded"


def test_bulk_upload_mixed_validation_errors(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5 valid + 2 oversized + 1 bad-MIME → response includes per-photo errors."""
    monkeypatch.setenv("TOYBOX_MAX_UPLOAD_BYTES", "1500")

    valid: list[tuple[bytes, str, str]] = [
        (_jpeg_bytes(color=(i * 13 % 256, 100, 50)), f"valid{i}.jpg", "image/jpeg")
        for i in range(5)
    ]
    # Use noisy 512x512 photos so JPEG payloads reliably exceed the
    # 1500-byte cap (a flat-colour image compresses to <2 KB). Each
    # oversized photo gets fresh random bytes so they have distinct
    # hashes — we want two ``validation_failed:upload_too_large`` rows,
    # not one + one ``duplicate_in_batch``.
    import secrets

    def _oversized() -> bytes:
        noise = secrets.token_bytes(512 * 512 * 3)
        oversized_img = Image.frombytes("RGB", (512, 512), noise)
        oversized_buf = io.BytesIO()
        oversized_img.save(oversized_buf, format="JPEG", quality=95)
        return oversized_buf.getvalue()

    big1 = _oversized()
    big2 = _oversized()
    assert len(big1) > 1500 and len(big2) > 1500  # sanity
    bad: list[tuple[bytes, str, str]] = [
        (big1, "big1.jpg", "image/jpeg"),
        (big2, "big2.jpg", "image/jpeg"),
        (b"hello world\n", "fake.jpg", "image/jpeg"),
    ]
    resp = vc_client.post(
        "/api/rooms/upload-bulk",
        files=_bulk_files(valid + bad),
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    photos = resp.json()["photos"]
    assert len(photos) == 8
    valid_ok = [p for p in photos if p["error"] is None]
    bad_too_big = [p for p in photos if p["error"] == "validation_failed:upload_too_large"]
    bad_mime = [p for p in photos if p["error"] == "validation_failed:upload_bad_mime"]
    assert len(valid_ok) == 5
    assert len(bad_too_big) == 2
    assert len(bad_mime) == 1


def test_bulk_upload_dedup_within_batch(
    vc_client: TestClient,
    parent_headers: dict[str, str],
) -> None:
    """Same photo twice — first wins, second has duplicate_in_batch."""
    photo = _jpeg_bytes(color=(99, 99, 99))
    resp = vc_client.post(
        "/api/rooms/upload-bulk",
        files=_bulk_files(
            [
                (photo, "first.jpg", "image/jpeg"),
                (photo, "second.jpg", "image/jpeg"),
            ]
        ),
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    photos = resp.json()["photos"]
    assert len(photos) == 2
    assert photos[0]["error"] is None
    assert photos[0]["staging_id"] != ""
    assert photos[1]["error"] == "duplicate_in_batch"


def test_bulk_upload_dedup_against_existing_room(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """A photo whose hash already lives in ``rooms`` → duplicate_existing_room."""
    photo = _jpeg_bytes(color=(11, 22, 33))
    from toybox.storage.images import compute_hash

    h = compute_hash(photo)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO rooms (id, display_name, image_path, image_hash) VALUES (?, ?, ?, ?)",
                ("existing-room-1", "Existing Living Room", "data/images/rooms/x.jpg", h),
            )
    finally:
        conn.close()

    resp = vc_client.post(
        "/api/rooms/upload-bulk",
        files=_bulk_files([(photo, "dup.jpg", "image/jpeg")]),
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    photo_resp = resp.json()["photos"][0]
    assert photo_resp["error"] == "duplicate_existing_room"
    assert photo_resp["existing_room"]["id"] == "existing-room-1"
    assert photo_resp["existing_room"]["display_name"] == "Existing Living Room"


# ---------------------------------------------------------------------
# Confirm-bulk
# ---------------------------------------------------------------------


def _do_bulk_upload(
    client: TestClient,
    headers: dict[str, str],
    payloads: list[tuple[bytes, str, str]],
) -> dict[str, Any]:
    """Helper: do an upload-bulk and return the parsed response body."""
    resp = client.post(
        "/api/rooms/upload-bulk",
        files=_bulk_files(payloads),
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return cast(dict[str, Any], resp.json())


def test_confirm_bulk_happy_path_existing_and_new_room(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    isolated_data_root: Path,
    db_path: Path,
) -> None:
    """3 photos: 1 → existing room, 2 → new room. DB rows + features written."""
    # Pre-seed an existing room.
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO rooms (id, display_name, image_path, image_hash) VALUES (?, ?, ?, ?)",
                (
                    "room-existing",
                    "Bedroom",
                    "data/images/rooms/seed.jpg",
                    "seed-hash",
                ),
            )
    finally:
        conn.close()

    payloads = [(_jpeg_bytes(color=(c, 100, 50)), f"r{c}.jpg", "image/jpeg") for c in (10, 20, 30)]
    upload = _do_bulk_upload(vc_client, parent_headers, payloads)
    batch_id = upload["batch_id"]
    sids = [p["staging_id"] for p in upload["photos"]]

    body = {
        "batch_id": batch_id,
        "assignments": [
            {
                "staging_id": sids[0],
                "room_id": "room-existing",
                "new_room_label": None,
                "features": [{"name": "bed"}],
            },
            {
                "staging_id": sids[1],
                "room_id": None,
                "new_room_label": "Living Room",
                "features": [{"name": "couch"}, {"name": "rug"}],
            },
            {
                "staging_id": sids[2],
                "room_id": None,
                "new_room_label": "Living Room",
                "features": [{"name": "lamp"}],
            },
        ],
    }
    resp = vc_client.post("/api/rooms/confirm-bulk", json=body, headers=parent_headers)
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert {r["display_name"] for r in out["rooms"]} == {"Bedroom", "Living Room"}
    feature_names = sorted(f["name"] for f in out["features"])
    assert feature_names == ["bed", "couch", "lamp", "rug"]

    # DB invariants:
    conn = connect(db_path)
    try:
        rooms = conn.execute("SELECT * FROM rooms ORDER BY display_name").fetchall()
        # Bedroom (existing) + Living Room (new) = 2 rooms.
        assert len(rooms) == 2
        # Living Room's image_path is the FIRST committed photo's path
        # (sids[1]). Verify the file exists on disk.
        living_row = conn.execute(
            "SELECT image_path FROM rooms WHERE display_name = ?",
            ("Living Room",),
        ).fetchone()
        living_path = isolated_data_root / living_row["image_path"].removeprefix("data/")
        assert living_path.is_file()
        # Both committed photos for Living Room exist on disk (the
        # gallery-sibling policy from the docstring).
        rooms_dir = isolated_data_root / "images" / "rooms"
        assert any(rooms_dir.glob(f"{sids[1]}.*"))
        assert any(rooms_dir.glob(f"{sids[2]}.*"))
        # Bedroom feature stayed under Bedroom.
        bed_feature = conn.execute(
            "SELECT * FROM room_features WHERE room_id = ?", ("room-existing",)
        ).fetchall()
        assert len(bed_feature) == 1
        assert bed_feature[0]["name"] == "bed"
    finally:
        conn.close()


def test_confirm_bulk_new_room_collision_returns_409(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """new_room_label collides (case-insensitive) with an existing room."""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO rooms (id, display_name, image_path, image_hash) VALUES (?, ?, ?, ?)",
                ("rid", "Living Room", "data/images/rooms/x.jpg", "h"),
            )
    finally:
        conn.close()

    upload = _do_bulk_upload(
        vc_client,
        parent_headers,
        [(_jpeg_bytes(color=(7, 7, 7)), "r.jpg", "image/jpeg")],
    )
    body = {
        "batch_id": upload["batch_id"],
        "assignments": [
            {
                "staging_id": upload["photos"][0]["staging_id"],
                "room_id": None,
                "new_room_label": "living room",
                "features": [],
            }
        ],
    }
    resp = vc_client.post("/api/rooms/confirm-bulk", json=body, headers=parent_headers)
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "room_label_collision"
    assert detail["existing_room"]["id"] == "rid"


# ---------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------


def test_list_returns_sorted_case_insensitive(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.executemany(
                "INSERT INTO rooms (id, display_name) VALUES (?, ?)",
                [
                    ("a", "Zen Den"),
                    ("b", "attic"),
                    ("c", "Bathroom"),
                ],
            )
    finally:
        conn.close()
    resp = vc_client.get("/api/rooms", headers=parent_headers)
    assert resp.status_code == 200
    names = [r["display_name"] for r in resp.json()["rooms"]]
    assert names == ["attic", "Bathroom", "Zen Den"]


def test_get_room_404_when_missing(vc_client: TestClient, parent_headers: dict[str, str]) -> None:
    resp = vc_client.get("/api/rooms/nope", headers=parent_headers)
    assert resp.status_code == 404


def test_patch_room_updates_display_name(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO rooms (id, display_name) VALUES (?, ?)",
                ("r1", "Old Name"),
            )
    finally:
        conn.close()
    resp = vc_client.patch(
        "/api/rooms/r1",
        json={"display_name": "New Name", "notes": "freshly painted"},
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["display_name"] == "New Name"
    assert body["notes"] == "freshly painted"


def test_room_list_and_get_serialize_room_type_and_active_defaults(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Phase X X1: a freshly-inserted room serializes the migration-0029
    defaults (room_type=None, active=True) on both list + get."""
    _seed_room(db_path, "r-defaults", "Playroom")
    list_resp = vc_client.get("/api/rooms", headers=parent_headers)
    assert list_resp.status_code == 200, list_resp.text
    room = next(r for r in list_resp.json()["rooms"] if r["id"] == "r-defaults")
    assert room["room_type"] is None
    assert room["active"] is True

    get_resp = vc_client.get("/api/rooms/r-defaults", headers=parent_headers)
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["room_type"] is None
    assert get_resp.json()["active"] is True


def test_patch_room_sets_and_returns_room_type_and_active(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """Phase X X1: PATCH persists + round-trips room_type + active."""
    _seed_room(db_path, "r-x1", "Kitchen")
    resp = vc_client.patch(
        "/api/rooms/r-x1",
        json={"room_type": "kitchen", "active": False},
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["room_type"] == "kitchen"
    assert body["active"] is False

    # Round-trip: a subsequent GET reflects the persisted values.
    get_resp = vc_client.get("/api/rooms/r-x1", headers=parent_headers)
    assert get_resp.json()["room_type"] == "kitchen"
    assert get_resp.json()["active"] is False

    # room_type can be cleared back to NULL with an explicit null.
    clear_resp = vc_client.patch(
        "/api/rooms/r-x1",
        json={"room_type": None},
        headers=parent_headers,
    )
    assert clear_resp.status_code == 200, clear_resp.text
    assert clear_resp.json()["room_type"] is None
    # active is untouched when not sent.
    assert clear_resp.json()["active"] is False


def test_delete_room_cascades_features(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """DELETE removes the room and its features in one transaction.

    The earlier behaviour was a 409 ``room_in_use`` whenever any
    feature still pointed at the room — but features are owned by the
    room (nothing else joins them in), so the parent had no way to
    delete an auto-suggested room that vision had populated. The
    handler now cascades; the FK is still RESTRICT at the schema
    level, so non-handler code paths are still protected.
    """
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO rooms (id, display_name) VALUES (?, ?)",
                ("r1", "Living Room"),
            )
            conn.execute(
                "INSERT INTO room_features (id, room_id, name) VALUES (?, ?, ?)",
                ("f1", "r1", "couch"),
            )
            conn.execute(
                "INSERT INTO room_features (id, room_id, name) VALUES (?, ?, ?)",
                ("f2", "r1", "rug"),
            )
    finally:
        conn.close()
    resp = vc_client.delete("/api/rooms/r1", headers=parent_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
    conn = connect(db_path)
    try:
        room_count = conn.execute(
            "SELECT COUNT(*) FROM rooms WHERE id = 'r1'"
        ).fetchone()[0]
        feature_count = conn.execute(
            "SELECT COUNT(*) FROM room_features WHERE room_id = 'r1'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert room_count == 0
    assert feature_count == 0


def test_delete_room_without_features_succeeds(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO rooms (id, display_name) VALUES (?, ?)",
                ("r1", "Empty Room"),
            )
    finally:
        conn.close()
    resp = vc_client.delete("/api/rooms/r1", headers=parent_headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_get_room_features_lists_for_room(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO rooms (id, display_name) VALUES (?, ?)",
                ("r1", "Living Room"),
            )
            conn.execute(
                "INSERT INTO room_features (id, room_id, name) VALUES (?, ?, ?)",
                ("f1", "r1", "rug"),
            )
            conn.execute(
                "INSERT INTO room_features (id, room_id, name) VALUES (?, ?, ?)",
                ("f2", "r1", "couch"),
            )
    finally:
        conn.close()
    resp = vc_client.get("/api/rooms/r1/features", headers=parent_headers)
    assert resp.status_code == 200
    names = [f["name"] for f in resp.json()["features"]]
    # Sorted case-insensitively.
    assert names == ["couch", "rug"]


# ---------------------------------------------------------------------
# Offline mode + capability gating
# ---------------------------------------------------------------------


def test_bulk_upload_offline_skips_vision(
    app: FastAPI,
    parent_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    stub_vision_client: StubClient,
) -> None:
    async def _not_capable(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        return (False, None)

    monkeypatch.setattr(rooms_router_mod, "is_capable", _not_capable)
    app.dependency_overrides[get_vision_client] = lambda: stub_vision_client
    with TestClient(app) as client:
        resp = client.post(
            "/api/rooms/upload-bulk",
            files=_bulk_files([(_jpeg_bytes(color=(1, 2, 3)), "r.jpg", "image/jpeg")]),
            headers=parent_headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["vision_skipped"] is True
    assert body["photos"][0]["suggested"] is None
    assert stub_vision_client.calls == []


def test_bulk_upload_no_token_skips_vision(app: FastAPI, parent_headers: dict[str, str]) -> None:
    app.dependency_overrides[get_vision_client] = lambda: None
    with TestClient(app) as client:
        resp = client.post(
            "/api/rooms/upload-bulk",
            files=_bulk_files([(_jpeg_bytes(color=(1, 2, 3)), "r.jpg", "image/jpeg")]),
            headers=parent_headers,
        )
    assert resp.status_code == 200
    assert resp.json()["vision_skipped"] is True


def test_bulk_upload_per_photo_vision_failure_records_error(
    app: FastAPI, parent_headers: dict[str, str]
) -> None:
    """A photo whose vision call returns malformed JSON keeps a
    ``vision_error`` string and remains in the response (parent
    assigns from the Unassigned tab)."""
    bad_client = StubClient(image_responses=["this is not JSON"])
    app.dependency_overrides[get_vision_client] = lambda: bad_client
    with TestClient(app) as client:
        resp = client.post(
            "/api/rooms/upload-bulk",
            files=_bulk_files([(_jpeg_bytes(color=(7, 7, 7)), "r.jpg", "image/jpeg")]),
            headers=parent_headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["vision_skipped"] is False
    photo = body["photos"][0]
    assert photo["suggested"] is None
    assert photo["vision_error"] == "malformed"
    # The photo still has a staging_id so the parent can manually assign it.
    assert photo["staging_id"] != ""


# ---------------------------------------------------------------------
# Janitor
# ---------------------------------------------------------------------


def test_janitor_purges_stale_staging_on_next_bulk_upload(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOYBOX_STAGING_TTL_SEC", "0")
    staging = isolated_data_root / "images" / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    stale = staging / "stale-room.jpg"
    stale.write_bytes(b"old-data")
    past = time.time() - 7200
    os.utime(stale, (past, past))
    assert stale.is_file()

    resp = vc_client.post(
        "/api/rooms/upload-bulk",
        files=_bulk_files([(_jpeg_bytes(), "r.jpg", "image/jpeg")]),
        headers=parent_headers,
    )
    assert resp.status_code == 200
    assert not stale.exists()


# ---------------------------------------------------------------------
# Vision concurrency
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vision_concurrency_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The semaphore must cap parallel vision calls at the configured
    limit. Exercises ``_run_vision_for_photo`` directly so we can probe
    the live concurrent count without booting the FastAPI test client.
    """

    monkeypatch.setenv("TOYBOX_VISION_CONCURRENCY", "4")

    in_flight = 0
    max_seen = 0
    lock = asyncio.Lock()

    async def fake_describe_image(
        self_: object,
        image_bytes: bytes,
        *,
        prompt: str,
        media_type: str = "image/png",
        max_tokens: int = 512,
    ) -> AIResponse:
        nonlocal in_flight, max_seen
        async with lock:
            in_flight += 1
            if in_flight > max_seen:
                max_seen = in_flight
        try:
            await asyncio.sleep(0.05)
            return AIResponse(
                text='{"suggested_room_label": "X", "features": []}',
                model="m",
            )
        finally:
            async with lock:
                in_flight -= 1

    class _CountingClient:
        async def complete_text(
            self, messages: object, *, max_tokens: int = 1024, system: str | None = None
        ) -> AIResponse:  # pragma: no cover
            raise NotImplementedError

        async def describe_image(
            self,
            image_bytes: bytes,
            *,
            prompt: str,
            media_type: str = "image/png",
            max_tokens: int = 512,
        ) -> AIResponse:
            return await fake_describe_image(
                self,
                image_bytes,
                prompt=prompt,
                media_type=media_type,
                max_tokens=max_tokens,
            )

    client = _CountingClient()
    sem = asyncio.Semaphore(rooms_router_mod.vision_concurrency())
    raw = _jpeg_bytes()
    tasks = [rooms_router_mod._run_vision_for_photo(sem, cast(Any, client), raw) for _ in range(20)]
    await asyncio.gather(*tasks)
    assert max_seen <= 4, f"saw {max_seen} concurrent vision calls (cap=4)"


# ---------------------------------------------------------------------
# Atomic rollback + per-photo isolation (iter-2 HIGH coverage)
# ---------------------------------------------------------------------


def _seed_room(db_path: Path, room_id: str, label: str) -> None:
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO rooms (id, display_name, image_path, image_hash) VALUES (?, ?, ?, ?)",
                (room_id, label, f"data/images/rooms/{room_id}.jpg", f"hash-{room_id}"),
            )
    finally:
        conn.close()


class _ConnWrapper:
    """Pass-through wrapper around a sqlite3.Connection.

    sqlite3.Connection.execute is C-level immutable, so we can't
    monkeypatch the method on the instance. Wrap the whole connection
    via FastAPI dep override and intercept execute() at the wrapper
    level. ``with conn:`` and the rest of the API still work because
    we delegate via ``__getattr__`` and explicit ``__enter__`` /
    ``__exit__`` proxies.
    """

    def __init__(self, real: sqlite3.Connection, intercept: Any) -> None:
        self._real = real
        self._intercept = intercept

    def execute(self, sql: str, params: Any = ()) -> Any:
        return self._intercept(self._real, sql, params)

    def __enter__(self) -> Any:
        return self._real.__enter__()

    def __exit__(self, *args: Any) -> Any:
        return self._real.__exit__(*args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _override_with_intercept(app: FastAPI, db_path: Path, intercept: Any) -> None:
    def _gen() -> Iterator[Any]:
        conn = connect(db_path, check_same_thread=False)
        try:
            yield _ConnWrapper(conn, intercept)
        finally:
            conn.close()

    app.dependency_overrides[get_vision_client] = app.dependency_overrides.get(
        get_vision_client
    ) or (lambda: None)
    from toybox.api.rooms import get_rooms_db as _get_rooms_db  # noqa: PLC0415

    app.dependency_overrides[_get_rooms_db] = _gen


def test_confirm_bulk_rolls_back_on_feature_insert_failure(
    app_with_vision: FastAPI,
    parent_headers: dict[str, str],
    isolated_data_root: Path,
    db_path: Path,
) -> None:
    """H1: feature insert raises on the 3rd call.

    Asserts (a) HTTP error, (b) every committed file is moved back to
    staging, (c) NO partial rooms / room_features rows leak past the
    rollback.
    """
    n_feature_inserts = 0
    sqlite3_mod = sqlite3

    def intercept(real: sqlite3.Connection, sql: str, params: Any = ()) -> Any:
        nonlocal n_feature_inserts
        if sql.strip().upper().startswith("INSERT INTO ROOM_FEATURES"):
            n_feature_inserts += 1
            if n_feature_inserts == 3:
                raise sqlite3_mod.IntegrityError("simulated unknown failure")
        return real.execute(sql, params)

    _override_with_intercept(app_with_vision, db_path, intercept)
    with TestClient(app_with_vision) as client:
        payloads = [
            (_jpeg_bytes(color=(c, 33, 99)), f"r{c}.jpg", "image/jpeg") for c in (10, 20, 30)
        ]
        upload = _do_bulk_upload(client, parent_headers, payloads)
        sids = [p["staging_id"] for p in upload["photos"]]
        body = {
            "batch_id": upload["batch_id"],
            "assignments": [
                {
                    "staging_id": sids[0],
                    "room_id": None,
                    "new_room_label": "Living Room",
                    "features": [{"name": "couch"}],
                },
                {
                    "staging_id": sids[1],
                    "room_id": None,
                    "new_room_label": "Living Room",
                    "features": [{"name": "rug"}],
                },
                {
                    "staging_id": sids[2],
                    "room_id": None,
                    "new_room_label": "Living Room",
                    "features": [{"name": "lamp"}],
                },
            ],
        }
        resp = client.post("/api/rooms/confirm-bulk", json=body, headers=parent_headers)
        assert resp.status_code in {422, 500}, resp.text

    # No room rows leaked.
    conn = connect(db_path)
    try:
        rooms = conn.execute("SELECT * FROM rooms").fetchall()
        assert rooms == []
        features = conn.execute("SELECT * FROM room_features").fetchall()
        assert features == []
    finally:
        conn.close()

    # All committed files are back in staging.
    staging_dir_path = isolated_data_root / "images" / ".staging"
    rooms_dir = isolated_data_root / "images" / "rooms"
    for sid in sids:
        if rooms_dir.exists():
            assert not list(rooms_dir.glob(f"{sid}.*")), (
                f"{sid} leaked into committed dir after rollback"
            )
        assert list(staging_dir_path.glob(f"{sid}.*")), f"{sid} missing from staging after rollback"


def test_confirm_bulk_fk_violation_distinct_from_unique(
    app_with_vision: FastAPI,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """H2: a real FK violation surfaces a different code than the
    UNIQUE(room_id, name) silent-dedup case (M2 fix verified).
    """
    sqlite3_mod = sqlite3

    def intercept(real: sqlite3.Connection, sql: str, params: Any = ()) -> Any:
        if sql.strip().upper().startswith("INSERT INTO ROOM_FEATURES"):
            raise sqlite3_mod.IntegrityError("FOREIGN KEY constraint failed")
        return real.execute(sql, params)

    _override_with_intercept(app_with_vision, db_path, intercept)
    with TestClient(app_with_vision) as client:
        payloads = [(_jpeg_bytes(color=(c, 33, 99)), f"r{c}.jpg", "image/jpeg") for c in (40,)]
        upload = _do_bulk_upload(client, parent_headers, payloads)
        sid = upload["photos"][0]["staging_id"]

        body = {
            "batch_id": upload["batch_id"],
            "assignments": [
                {
                    "staging_id": sid,
                    "room_id": None,
                    "new_room_label": "Pantry",
                    "features": [{"name": "shelves"}],
                }
            ],
        }
        resp = client.post("/api/rooms/confirm-bulk", json=body, headers=parent_headers)
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        # M2 guarantee: FK message yields `invalid_room_id`, distinct
        # from the silent unique-collision dedup path (which returns
        # 201). Tested separately by
        # ``test_confirm_bulk_unique_feature_dedup_silently_swallowed``.
        assert detail["code"] == "invalid_room_id"

    # No partial rows.
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM room_features").fetchone()[0] == 0
    finally:
        conn.close()


def test_confirm_bulk_unique_feature_dedup_silently_swallowed(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
) -> None:
    """H2 partner: the UNIQUE(room_id, name) collision is silently
    skipped (one ``couch`` instead of two) — distinct from FK fail.
    """
    payloads = [(_jpeg_bytes(color=(c, 88, 11)), f"r{c}.jpg", "image/jpeg") for c in (50,)]
    upload = _do_bulk_upload(vc_client, parent_headers, payloads)
    sid = upload["photos"][0]["staging_id"]

    body = {
        "batch_id": upload["batch_id"],
        "assignments": [
            {
                "staging_id": sid,
                "room_id": None,
                "new_room_label": "Loft",
                # Same feature twice on the same room — the second hits
                # the UNIQUE(room_id, name) constraint and gets quietly
                # dropped.
                "features": [{"name": "couch"}, {"name": "couch"}],
            }
        ],
    }
    resp = vc_client.post("/api/rooms/confirm-bulk", json=body, headers=parent_headers)
    assert resp.status_code == 201, resp.text
    # Only one couch landed.
    conn = connect(db_path)
    try:
        feats = conn.execute("SELECT name FROM room_features").fetchall()
        assert [f["name"] for f in feats] == ["couch"]
    finally:
        conn.close()


def test_confirm_bulk_three_photos_one_new_room_invariants(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    isolated_data_root: Path,
    db_path: Path,
) -> None:
    """H3: three photos → ONE new room ``Living Room``.

    Locks the documented invariants:
    (a) exactly 1 row in ``rooms``,
    (b) ``rooms.image_path`` is the FIRST-committed photo's path,
    (c) all 3 photo files exist on disk (gallery siblings).
    """
    payloads = [
        (_jpeg_bytes(color=(c * 7 % 256, c * 11 % 256, c * 13 % 256)), f"r{c}.jpg", "image/jpeg")
        for c in (15, 99, 200)
    ]
    upload = _do_bulk_upload(vc_client, parent_headers, payloads)
    sids = [p["staging_id"] for p in upload["photos"]]
    # Sanity: the helper photos must dedup distinct, not collide.
    assert all(sid != "" for sid in sids), upload["photos"]

    body = {
        "batch_id": upload["batch_id"],
        "assignments": [
            {
                "staging_id": sids[0],
                "room_id": None,
                "new_room_label": "Living Room",
                "features": [{"name": "couch"}],
            },
            {
                "staging_id": sids[1],
                "room_id": None,
                "new_room_label": "Living Room",
                "features": [{"name": "rug"}],
            },
            {
                "staging_id": sids[2],
                "room_id": None,
                "new_room_label": "Living Room",
                "features": [{"name": "lamp"}],
            },
        ],
    }
    resp = vc_client.post("/api/rooms/confirm-bulk", json=body, headers=parent_headers)
    assert resp.status_code == 201, resp.text

    conn = connect(db_path)
    try:
        rooms = conn.execute("SELECT * FROM rooms").fetchall()
        assert len(rooms) == 1
        # image_path is the FIRST committed photo (sids[0]).
        assert sids[0] in rooms[0]["image_path"]
    finally:
        conn.close()

    rooms_dir = isolated_data_root / "images" / "rooms"
    for sid in sids:
        assert list(rooms_dir.glob(f"{sid}.*")), f"{sid} not on disk"


def test_run_vision_for_photo_isolates_per_photo_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M1 verifier: even when a vision task raises, the gather loop
    stays alive and that one photo simply ends with vision_error=error.

    Uses _run_vision_for_photo directly so we can force one task to
    raise mid-flight without booting Pillow.
    """
    sem = asyncio.Semaphore(2)
    good_text = '{"suggested_room_label": "Den", "features": []}'

    class _MaybeRaisingClient:
        def __init__(self, raise_on_byte: int) -> None:
            self._raise = raise_on_byte

        async def complete_text(
            self, messages: object, *, max_tokens: int = 1024, system: str | None = None
        ) -> AIResponse:  # pragma: no cover
            raise NotImplementedError

        async def describe_image(
            self,
            image_bytes: bytes,
            *,
            prompt: str,
            media_type: str = "image/png",
            max_tokens: int = 512,
        ) -> AIResponse:
            if image_bytes[:1] == bytes([self._raise]):
                raise RuntimeError("synthetic vision crash")
            return AIResponse(text=good_text, model="m")

    # Build 3 calls; the middle one's bytes start with 0xFE so that
    # client raises RuntimeError on it. The other two succeed.
    raw_ok = _jpeg_bytes(color=(0, 1, 2))
    raw_bad_prefix = bytes([0xFE]) + raw_ok[1:]
    client = cast(Any, _MaybeRaisingClient(raise_on_byte=0xFE))

    async def _run() -> tuple[Any, Any, Any]:
        # Force the downscaler to be a no-op so we can craft the bytes
        # directly without surviving Pillow.
        monkeypatch.setattr(rooms_router_mod, "downscale_for_vision", lambda b: b)
        return await asyncio.gather(
            rooms_router_mod._run_vision_for_photo(sem, client, raw_ok),
            rooms_router_mod._run_vision_for_photo(sem, client, raw_bad_prefix),
            rooms_router_mod._run_vision_for_photo(sem, client, raw_ok),
        )

    from toybox.ai.house_vision import HouseVisionSuggestion as _Sugg  # noqa: PLC0415

    results = asyncio.run(_run())
    # Two suggestions + one error tuple — gather did NOT cancel siblings.
    assert isinstance(results[0], _Sugg)
    assert results[1] == (None, "error")
    assert isinstance(results[2], _Sugg)


def test_confirm_bulk_oserror_in_commit_triggers_rollback(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    isolated_data_root: Path,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M3 verifier: ``commit_staging`` raises OSError("ENOSPC") on the
    3rd of 5 assignments. Asserts (a) HTTP 503, (b) committed files (the
    canonicals from the first 2 new rooms) move back to staging, (c)
    NO rooms / room_features rows persisted.
    """
    colors = (60, 70, 80, 90, 100)
    payloads = [(_jpeg_bytes(color=(c, 7, 1)), f"r{c}.jpg", "image/jpeg") for c in colors]
    upload = _do_bulk_upload(vc_client, parent_headers, payloads)
    sids = [p["staging_id"] for p in upload["photos"]]

    # Ensure 5 different new rooms (so each assignment is a "canonical"
    # commit and the 3rd ENOSPC interrupts after 2 successful moves).
    from toybox.storage import images as _images  # noqa: PLC0415

    real_commit = _images.commit_staging
    call_n = 0

    def patched_commit(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_n
        call_n += 1
        if call_n == 3:
            raise OSError("[Errno 28] No space left on device")
        return real_commit(*args, **kwargs)

    monkeypatch.setattr(rooms_router_mod, "commit_staging", patched_commit)

    body = {
        "batch_id": upload["batch_id"],
        "assignments": [
            {
                "staging_id": sids[i],
                "room_id": None,
                "new_room_label": f"Room {i}",
                "features": [],
            }
            for i in range(5)
        ],
    }
    resp = vc_client.post("/api/rooms/confirm-bulk", json=body, headers=parent_headers)
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "commit_failed"

    # No DB rows.
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM room_features").fetchone()[0] == 0
    finally:
        conn.close()

    # Committed dir holds none of these sids; staging holds all 5.
    rooms_dir = isolated_data_root / "images" / "rooms"
    staging_dir_path = isolated_data_root / "images" / ".staging"
    for sid in sids:
        if rooms_dir.exists():
            assert not list(rooms_dir.glob(f"{sid}.*")), f"{sid} leaked into rooms dir"
        assert list(staging_dir_path.glob(f"{sid}.*")), f"{sid} not back in staging"


def test_confirm_bulk_existing_room_does_not_orphan_files(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    isolated_data_root: Path,
    db_path: Path,
) -> None:
    """L9 verifier: 3 photos all assigned to an EXISTING room.

    The existing room's ``image_path`` already points to its seed
    photo — none of the 3 uploads should land in
    ``data/images/rooms/`` because the schema has nowhere to
    reference them.
    """
    _seed_room(db_path, "room-existing", "Bedroom")
    colors = (110, 120, 130)
    payloads = [(_jpeg_bytes(color=(c, 11, 22)), f"r{c}.jpg", "image/jpeg") for c in colors]
    upload = _do_bulk_upload(vc_client, parent_headers, payloads)
    sids = [p["staging_id"] for p in upload["photos"]]

    body = {
        "batch_id": upload["batch_id"],
        "assignments": [
            {
                "staging_id": sid,
                "room_id": "room-existing",
                "new_room_label": None,
                "features": [],
            }
            for sid in sids
        ],
    }
    resp = vc_client.post("/api/rooms/confirm-bulk", json=body, headers=parent_headers)
    assert resp.status_code == 201, resp.text

    rooms_dir = isolated_data_root / "images" / "rooms"
    for sid in sids:
        if rooms_dir.exists():
            assert not list(rooms_dir.glob(f"{sid}.*")), (
                f"{sid} should NOT have been committed to rooms dir for existing-room assignment"
            )
