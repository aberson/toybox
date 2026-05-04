"""Integration tests for the Step 16 toy ingest pipeline.

These boot the full FastAPI app via the conftest fixture, mock the
Claude vision client to a deterministic stub, and exercise the
upload → confirm → list/patch/delete flow end-to-end.
"""

from __future__ import annotations

import io
import os
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from toybox.ai import capability as capability_mod
from toybox.ai.client import StubClient
from toybox.api import toys as toys_router_mod
from toybox.api.toys import get_vision_client
from toybox.db.connection import connect

# ---------------------------------------------------------------------
# Fixtures: deterministic vision + isolated data root + helpers
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def reset_staging_registry() -> Iterator[None]:
    """Each test starts with an empty staging registry."""
    toys_router_mod._staging_extensions.clear()
    yield
    toys_router_mod._staging_extensions.clear()


@pytest.fixture(autouse=True)
def stub_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``is_capable`` to return True so vision runs by default."""

    async def _capable(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        return (True, None)

    monkeypatch.setattr(capability_mod, "is_capable", _capable)
    monkeypatch.setattr(toys_router_mod, "is_capable", _capable)


@pytest.fixture
def stub_vision_client() -> StubClient:
    """Return a StubClient pre-seeded with a successful vision response."""
    return StubClient(
        image_responses=[
            '{"display_name": "Sparkle Unicorn", '
            '"tags": ["plush", "unicorn", "pink"], '
            '"persona_match_id": null}'
        ]
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


def _png_bytes(size: tuple[int, int] = (64, 64)) -> bytes:
    img = Image.new("RGB", size, (50, 100, 200))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _upload_files(
    payload: bytes,
    filename: str = "toy.jpg",
    mime: str = "image/jpeg",
) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (filename, payload, mime)}


# ---------------------------------------------------------------------
# Auth gating: every endpoint must reject anonymous + child-scope tokens
# ---------------------------------------------------------------------


_PROTECTED_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/api/toys/upload", None),
    ("GET", "/api/toys", None),
    ("POST", "/api/toys", {"staging_id": "x", "display_name": "X", "tags": []}),
    ("GET", "/api/toys/abc", None),
    ("PATCH", "/api/toys/abc", {"display_name": "Y"}),
    ("DELETE", "/api/toys/abc", None),
]


@pytest.mark.parametrize(("method", "path", "body"), _PROTECTED_ENDPOINTS)
def test_endpoints_require_parent_token(
    vc_client: TestClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    """Every toy endpoint must 401 without a token. Mirrors children."""
    if method == "POST" and path.endswith("/upload"):
        # Multipart endpoints need a file part; auth fires before
        # multipart parsing so a tiny payload is enough.
        response = vc_client.post(path, files=_upload_files(b"x"))
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
    """Child-scope tokens must not have access to the toy endpoints."""
    headers = {"Authorization": f"Bearer {child_token}"}
    if method == "POST" and path.endswith("/upload"):
        response = vc_client.post(path, files=_upload_files(b"x"), headers=headers)
    else:
        response = vc_client.request(method, path, json=body, headers=headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------
# Upload happy path + error paths
# ---------------------------------------------------------------------


def test_upload_returns_suggested_fields(
    vc_client: TestClient, parent_headers: dict[str, str]
) -> None:
    resp = vc_client.post(
        "/api/toys/upload",
        files=_upload_files(_jpeg_bytes()),
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "staging_id" in body and isinstance(body["staging_id"], str)
    assert "image_hash" in body
    assert body["suggested"]["display_name"] == "Sparkle Unicorn"
    assert body["suggested"]["tags"] == ["plush", "unicorn", "pink"]
    assert body["vision_skipped"] is False
    assert body["vision_error"] is None
    assert body["media_type"] == "image/jpeg"


def test_upload_too_large_bytes(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOYBOX_MAX_UPLOAD_BYTES", "100")
    resp = vc_client.post(
        "/api/toys/upload",
        files=_upload_files(_jpeg_bytes((128, 128))),
        headers=parent_headers,
    )
    assert resp.status_code == 413
    assert resp.json()["detail"]["code"] == "upload_too_large"


def test_upload_bad_mime_text_with_jpeg_extension(
    vc_client: TestClient, parent_headers: dict[str, str]
) -> None:
    resp = vc_client.post(
        "/api/toys/upload",
        files=_upload_files(b"hello world\n", filename="fake.jpg", mime="image/jpeg"),
        headers=parent_headers,
    )
    assert resp.status_code == 415
    assert resp.json()["detail"]["code"] == "upload_bad_mime"


def test_upload_too_large_dimensions(vc_client: TestClient, parent_headers: dict[str, str]) -> None:
    resp = vc_client.post(
        "/api/toys/upload",
        files=_upload_files(_jpeg_bytes((10000, 10))),
        headers=parent_headers,
    )
    assert resp.status_code == 413
    assert resp.json()["detail"]["code"] == "upload_too_large_dimensions"


# ---------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------


def test_upload_409_on_duplicate_hash(
    vc_client: TestClient, parent_headers: dict[str, str], db_path: Path
) -> None:
    """Pre-seed a non-archived toy with the upload's hash, then upload."""
    payload = _jpeg_bytes((48, 48), color=(120, 80, 30))
    # Compute its hash inline using the same function the helper uses.
    from toybox.storage.images import compute_hash

    h = compute_hash(payload)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO toys (id, display_name, image_path, image_hash, "
                "tags, archived, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                (
                    "existing-1",
                    "Original Toy",
                    "data/images/toys/existing-1.jpg",
                    h,
                    "plush",
                    "2026-01-01T00:00:00Z",
                ),
            )
    finally:
        conn.close()

    resp = vc_client.post(
        "/api/toys/upload",
        files=_upload_files(payload),
        headers=parent_headers,
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "image_already_exists"
    assert detail["existing_toy"]["id"] == "existing-1"
    assert detail["existing_toy"]["display_name"] == "Original Toy"


def test_archived_toy_does_not_block_reingest(
    vc_client: TestClient, parent_headers: dict[str, str], db_path: Path
) -> None:
    payload = _jpeg_bytes((48, 48), color=(120, 80, 30))
    from toybox.storage.images import compute_hash

    h = compute_hash(payload)
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO toys (id, display_name, image_path, image_hash, "
                "tags, archived, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (
                    "old-1",
                    "Archived Toy",
                    "data/images/toys/old-1.jpg",
                    h,
                    "plush",
                    "2026-01-01T00:00:00Z",
                ),
            )
    finally:
        conn.close()
    resp = vc_client.post(
        "/api/toys/upload",
        files=_upload_files(payload),
        headers=parent_headers,
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------


def test_confirm_moves_file_and_inserts_row(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    isolated_data_root: Path,
) -> None:
    upload_resp = vc_client.post(
        "/api/toys/upload",
        files=_upload_files(_jpeg_bytes()),
        headers=parent_headers,
    )
    assert upload_resp.status_code == 200, upload_resp.text
    staging_id = upload_resp.json()["staging_id"]

    confirm_resp = vc_client.post(
        "/api/toys",
        json={
            "staging_id": staging_id,
            "display_name": "Sparkle Unicorn",
            "tags": ["plush", "unicorn"],
            "persona_id": None,
        },
        headers=parent_headers,
    )
    assert confirm_resp.status_code == 201, confirm_resp.text
    body = confirm_resp.json()
    assert body["display_name"] == "Sparkle Unicorn"
    assert body["tags"] == ["plush", "unicorn"]
    assert body["archived"] is False
    assert body["image_path"].startswith("data/images/toys/")

    # The on-disk path is the data-root + the relative-without-data-prefix
    # (the wire shape always starts with ``data/`` regardless of root).
    relative_no_prefix = body["image_path"].removeprefix("data/")
    final_path = isolated_data_root / relative_no_prefix
    assert final_path.is_file()
    staging_path = isolated_data_root / "images" / ".staging"
    assert not any(staging_path.glob(f"{staging_id}.*"))


def test_confirm_unknown_staging_id_returns_404(
    vc_client: TestClient, parent_headers: dict[str, str]
) -> None:
    resp = vc_client.post(
        "/api/toys",
        json={"staging_id": "deadbeef", "display_name": "X", "tags": []},
        headers=parent_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "staging_not_found"


def test_confirm_invalid_persona_id_returns_422(
    vc_client: TestClient, parent_headers: dict[str, str]
) -> None:
    """FK violation on persona_id → 422 invalid_persona_id (regression: H1).

    Iter-1 unconditionally translated every IntegrityError to 409
    image_already_exists with no ``existing_toy``, masking the
    foreign-key failure as a confusing dedup hit.
    """
    upload = vc_client.post(
        "/api/toys/upload",
        files=_upload_files(_jpeg_bytes(color=(33, 99, 200))),
        headers=parent_headers,
    )
    assert upload.status_code == 200, upload.text
    staging_id = upload.json()["staging_id"]

    resp = vc_client.post(
        "/api/toys",
        json={
            "staging_id": staging_id,
            "display_name": "Bear",
            "tags": [],
            "persona_id": "does-not-exist",
        },
        headers=parent_headers,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_persona_id"


def test_confirm_rolls_back_file_on_db_error(
    app_with_vision: FastAPI,
    parent_headers: dict[str, str],
    isolated_data_root: Path,
    db_path: Path,
) -> None:
    """If the DB INSERT fails after commit_staging, the file is unlinked.

    Regression test for the orphan-file path the iter-1 reviewer
    flagged as untested. We override ``get_toys_db`` with a connection
    wrapper that intercepts the ``INSERT INTO toys`` execute so it
    raises IntegrityError. The post-rollback unlink should leave no
    file at ``data/images/toys/<staging_id>.<ext>``.
    """
    from toybox.api.toys import get_toys_db

    class _RaisingConn:
        """Proxy that re-raises IntegrityError on the toys INSERT."""

        def __init__(self, real: sqlite3.Connection) -> None:
            self._real = real

        def execute(self, sql: str, *args: Any) -> Any:
            if sql.lstrip().upper().startswith("INSERT INTO TOYS"):
                raise sqlite3.IntegrityError("forced for test")
            return self._real.execute(sql, *args)

        # Forward everything else (including ``__enter__``/``__exit__``
        # for the ``with conn:`` transaction block) to the real connection.
        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

        def __enter__(self) -> Any:
            return self._real.__enter__()

        def __exit__(self, *exc: Any) -> Any:
            return self._real.__exit__(*exc)

    def _override() -> Iterator[Any]:
        conn = connect(db_path, check_same_thread=False)
        try:
            yield _RaisingConn(conn)
        finally:
            conn.close()

    app_with_vision.dependency_overrides[get_toys_db] = _override
    try:
        with TestClient(app_with_vision) as client:
            upload = client.post(
                "/api/toys/upload",
                files=_upload_files(_jpeg_bytes(color=(7, 11, 13))),
                headers=parent_headers,
            )
            # The upload endpoint also uses ``get_toys_db``, but the
            # _RaisingConn proxy only intercepts INSERT INTO TOYS — the
            # SELECT-based dedup probe goes through. Confirm the upload
            # succeeded so we have a staging_id.
            assert upload.status_code == 200, upload.text
            staging_id = upload.json()["staging_id"]

            resp = client.post(
                "/api/toys",
                json={"staging_id": staging_id, "display_name": "Bear", "tags": []},
                headers=parent_headers,
            )
    finally:
        app_with_vision.dependency_overrides.pop(get_toys_db, None)
    # The fake INSERT IntegrityError isn't a FK violation and there's
    # no existing dedup row, so we land in the generic
    # ``db_constraint_violation`` 422 branch.
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "db_constraint_violation"

    toys_dir = isolated_data_root / "images" / "toys"
    if toys_dir.exists():
        leftovers = list(toys_dir.glob(f"{staging_id}.*"))
        assert leftovers == [], f"orphan file left after rollback: {leftovers}"


# ---------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------


def _create_toy(client: TestClient, headers: dict[str, str], name: str = "Bear") -> str:
    """Helper: upload + confirm, return new toy id. Each call generates
    a unique image (via random colour) so dedup doesn't fire."""
    import secrets

    color = (secrets.randbits(8), secrets.randbits(8), secrets.randbits(8))
    upload = client.post(
        "/api/toys/upload",
        files=_upload_files(_jpeg_bytes(color=color)),
        headers=headers,
    )
    assert upload.status_code == 200, upload.text
    staging_id = upload.json()["staging_id"]
    confirm = client.post(
        "/api/toys",
        json={"staging_id": staging_id, "display_name": name, "tags": []},
        headers=headers,
    )
    assert confirm.status_code == 201, confirm.text
    return str(confirm.json()["id"])


def test_list_returns_sorted_non_archived(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    stub_vision_client: StubClient,
) -> None:
    # Pre-seed image_responses for the multiple uploads.
    stub_vision_client._image_responses = [
        '{"display_name": "X", "tags": [], "persona_match_id": null}'
    ] * 5

    a = _create_toy(vc_client, parent_headers, "Zebra")
    b = _create_toy(vc_client, parent_headers, "Apple")
    c = _create_toy(vc_client, parent_headers, "mango")

    # Archive Apple
    archive = vc_client.delete(f"/api/toys/{b}", headers=parent_headers)
    assert archive.status_code == 200

    resp = vc_client.get("/api/toys", headers=parent_headers)
    assert resp.status_code == 200
    names = [t["display_name"] for t in resp.json()["toys"]]
    # Apple was archived; mango and Zebra remain, sorted case-insensitively.
    assert names == ["mango", "Zebra"]
    assert a in {t["id"] for t in resp.json()["toys"]}
    assert c in {t["id"] for t in resp.json()["toys"]}


def test_get_404_on_missing(vc_client: TestClient, parent_headers: dict[str, str]) -> None:
    resp = vc_client.get("/api/toys/no-such-id", headers=parent_headers)
    assert resp.status_code == 404


def test_patch_updates_fields(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    stub_vision_client: StubClient,
) -> None:
    stub_vision_client._image_responses = [
        '{"display_name": "X", "tags": [], "persona_match_id": null}'
    ]
    toy_id = _create_toy(vc_client, parent_headers, "Original")
    resp = vc_client.patch(
        f"/api/toys/{toy_id}",
        json={"display_name": "Renamed", "tags": ["fluffy"]},
        headers=parent_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "Renamed"
    assert body["tags"] == ["fluffy"]


def test_patch_rejects_null_display_name(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    stub_vision_client: StubClient,
) -> None:
    """PATCH with ``{"display_name": null}`` must 422, not 500.

    Regression for M1: ``display_name`` is NOT NULL in the schema, so
    the previous validator's ``None`` short-circuit produced
    ``UPDATE toys SET display_name = NULL`` and IntegrityError → 500.
    """
    stub_vision_client._image_responses = [
        '{"display_name": "X", "tags": [], "persona_match_id": null}'
    ]
    toy_id = _create_toy(vc_client, parent_headers, "KeepMe")
    resp = vc_client.patch(
        f"/api/toys/{toy_id}",
        json={"display_name": None},
        headers=parent_headers,
    )
    assert resp.status_code == 422, resp.text


def test_delete_archives_but_keeps_file(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    isolated_data_root: Path,
    stub_vision_client: StubClient,
) -> None:
    stub_vision_client._image_responses = [
        '{"display_name": "X", "tags": [], "persona_match_id": null}'
    ]
    toy_id = _create_toy(vc_client, parent_headers, "ToArchive")
    get_resp = vc_client.get(f"/api/toys/{toy_id}", headers=parent_headers)
    image_path = isolated_data_root / get_resp.json()["image_path"].removeprefix("data/")
    assert image_path.is_file()

    resp = vc_client.delete(f"/api/toys/{toy_id}", headers=parent_headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "archived": True}

    # Row archived, file still present.
    assert image_path.is_file()


# ---------------------------------------------------------------------
# Offline / capability gating
# ---------------------------------------------------------------------


def test_upload_offline_skips_vision(
    app: FastAPI,
    parent_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    stub_vision_client: StubClient,
) -> None:
    """When ``is_capable`` returns False, no vision call is made."""

    async def _not_capable(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        return (False, None)

    monkeypatch.setattr(toys_router_mod, "is_capable", _not_capable)
    app.dependency_overrides[get_vision_client] = lambda: stub_vision_client
    with TestClient(app) as client:
        resp = client.post(
            "/api/toys/upload",
            files=_upload_files(_jpeg_bytes()),
            headers=parent_headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["suggested"] is None
    assert body["vision_skipped"] is True
    assert body["vision_error"] is None
    # Stub was never called.
    assert stub_vision_client.calls == []


def test_upload_no_token_skips_vision(app: FastAPI, parent_headers: dict[str, str]) -> None:
    """No OAuth token → ``get_vision_client`` returns None → vision_skipped."""
    app.dependency_overrides[get_vision_client] = lambda: None
    with TestClient(app) as client:
        resp = client.post(
            "/api/toys/upload",
            files=_upload_files(_jpeg_bytes()),
            headers=parent_headers,
        )
    assert resp.status_code == 200
    assert resp.json()["vision_skipped"] is True


def test_vision_failure_returns_error_string(app: FastAPI, parent_headers: dict[str, str]) -> None:
    bad_client = StubClient(image_responses=["this is not JSON"])
    app.dependency_overrides[get_vision_client] = lambda: bad_client
    with TestClient(app) as client:
        resp = client.post(
            "/api/toys/upload",
            files=_upload_files(_jpeg_bytes()),
            headers=parent_headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["suggested"] is None
    assert body["vision_error"] == "malformed"
    assert body["vision_skipped"] is False


# ---------------------------------------------------------------------
# Trigger registry
# ---------------------------------------------------------------------


def test_mention_toy_trigger_fires_after_commit(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    stub_vision_client: StubClient,
) -> None:
    """End-to-end: insert a toy, the mention_toy regex picks it up."""
    stub_vision_client._image_responses = [
        '{"display_name": "X", "tags": [], "persona_match_id": null}'
    ]
    toy_id = _create_toy(vc_client, parent_headers, "Sparkle Unicorn")

    from toybox.triggers.registry import match

    intents = match(
        "I really want to play with sparkle unicorn please",
        db_path=db_path,
    )
    matching = [i for i in intents if i.name == "mention_toy" and i.slot == "Sparkle Unicorn"]
    assert len(matching) == 1
    assert matching[0].pattern_id.endswith(toy_id)


# ---------------------------------------------------------------------
# Janitor
# ---------------------------------------------------------------------


def test_janitor_purges_stale_staging_on_next_upload(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set TTL to 0 + write a fake stale file, then upload — file should vanish."""
    monkeypatch.setenv("TOYBOX_STAGING_TTL_SEC", "0")
    staging = isolated_data_root / "images" / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    stale = staging / "stale-marker.jpg"
    stale.write_bytes(b"old-data")
    # Backdate the file to be safe.
    past = time.time() - 7200
    os.utime(stale, (past, past))
    assert stale.is_file()

    resp = vc_client.post(
        "/api/toys/upload",
        files=_upload_files(_jpeg_bytes()),
        headers=parent_headers,
    )
    assert resp.status_code == 200
    assert not stale.exists()


def test_staging_registry_evicts_abandoned_entries(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An upload whose confirm never arrives must have its registry
    entry evicted on the next upload (TTL-aware sweep).

    Regression for M4: the in-memory ``_staging_extensions`` dict
    previously grew unbounded across the process lifetime.
    """
    # First upload populates the registry.
    upload = vc_client.post(
        "/api/toys/upload",
        files=_upload_files(_jpeg_bytes(color=(11, 22, 33))),
        headers=parent_headers,
    )
    assert upload.status_code == 200
    abandoned = upload.json()["staging_id"]
    assert abandoned in toys_router_mod._staging_extensions

    # Drop TTL to 0 so the next upload's sweep evicts everything older
    # than now (the registry entry was inserted before the upload
    # below, so its timestamp is < now).
    monkeypatch.setenv("TOYBOX_STAGING_TTL_SEC", "0")
    # Sleep a tick so the second upload's "now - 0" cutoff is strictly
    # past the first entry's timestamp.
    time.sleep(0.01)

    second = vc_client.post(
        "/api/toys/upload",
        files=_upload_files(_jpeg_bytes(color=(99, 200, 1))),
        headers=parent_headers,
    )
    assert second.status_code == 200
    # The abandoned entry has been swept; only the fresh one remains.
    assert abandoned not in toys_router_mod._staging_extensions
