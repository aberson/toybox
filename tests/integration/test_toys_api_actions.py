"""Integration tests for the Phase F Step F5 action-sprite endpoints.

Covers the three new REST endpoints on :mod:`toybox.api.toys`:

* ``GET /api/toys/{id}/actions``
* ``POST /api/toys/{id}/actions/regenerate``
* ``POST /api/toys/{id}/actions/{slot}/regenerate``

Plus the post-commit hook on ``POST /api/toys`` that enqueues 10
image-gen jobs after a successful staging commit. The hook test is the
non-negotiable integration-through-the-production-handler check
demanded by ``feedback_buildstep_require_integration_test.md`` (Phase E
memory): the test drives the production toy-create REST handler
end-to-end and asserts 10 jobs land in the worker queue via the
captured stub.
"""

from __future__ import annotations

import io
import logging
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from toybox.ai import capability as capability_mod
from toybox.ai.client import StubClient
from toybox.api import toys as toys_router_mod
from toybox.api.toys import get_vision_client
from toybox.db.connection import connect
from toybox.image_gen import worker as worker_module
from toybox.image_gen.models import ACTION_SLOTS

# A canonical UUIDv4 we'll use for the seeded toy.
_SEEDED_TOY_ID = "550e8400-e29b-41d4-a716-446655440000"


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def reset_staging_registry() -> Iterator[None]:
    toys_router_mod._staging_extensions.clear()
    yield
    toys_router_mod._staging_extensions.clear()


@pytest.fixture(autouse=True)
def reset_worker_singleton() -> Iterator[None]:
    """Drop any worker singleton set by a previous test.

    The ``_worker`` global on :mod:`toybox.image_gen.worker` survives
    between tests in the same process; without this fixture, an
    earlier integration test's real worker could leak into ours.
    """
    worker_module._worker = None
    yield
    worker_module._worker = None


@pytest.fixture(autouse=True)
def stub_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the AI capability gate to True so vision runs by default.

    Distinct from ``is_image_gen_capable`` (Phase F's gate) which the
    individual tests below set per-case. This one matches the existing
    ``test_toys_api`` fixture.
    """

    async def _capable(*_args: object, **_kwargs: object) -> tuple[bool, str | None]:
        return (True, None)

    monkeypatch.setattr(capability_mod, "is_capable", _capable)
    monkeypatch.setattr(toys_router_mod, "is_capable", _capable)


@pytest.fixture
def stub_vision_client() -> StubClient:
    return StubClient(
        image_responses=[
            '{"display_name": "Sparkle Unicorn", '
            '"tags": ["plush", "unicorn", "pink"], '
            '"persona_match_id": null}'
        ]
    )


@pytest.fixture
def app_with_vision(app: FastAPI, stub_vision_client: StubClient) -> FastAPI:
    app.dependency_overrides[get_vision_client] = lambda: stub_vision_client
    return app


@pytest.fixture
def vc_client(app_with_vision: FastAPI) -> Iterator[TestClient]:
    with TestClient(app_with_vision) as test_client:
        yield test_client


class _StubWorker:
    """Captures every :meth:`enqueue` call without touching the queue.

    Mirrors :class:`toybox.image_gen.worker.ImageGenWorker`'s public
    ``enqueue`` signature so the F5 endpoints + commit hook can call
    it identically to production. Tests assert against
    :attr:`enqueued`.
    """

    def __init__(self, *, raise_exc: BaseException | None = None) -> None:
        self.enqueued: list[tuple[str, str, int | None]] = []
        self._raise_exc = raise_exc

    async def enqueue(
        self,
        toy_id: str,
        slot: str,
        *,
        seed: int | None = None,
    ) -> None:
        if self._raise_exc is not None:
            raise self._raise_exc
        self.enqueued.append((toy_id, slot, seed))


@pytest.fixture
def stub_worker(monkeypatch: pytest.MonkeyPatch) -> _StubWorker:
    """Install a recording worker on the singleton seam.

    Both the REST endpoints and the post-commit hook resolve the worker
    via :func:`toybox.image_gen.worker.get_image_gen_worker`. Setting
    the module-level ``_worker`` lets us thread the stub through both
    seams without touching the lifespan or starting a real worker.
    """
    stub = _StubWorker()
    monkeypatch.setattr(worker_module, "_worker", stub, raising=False)
    return stub


@pytest.fixture
def no_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force :func:`get_image_gen_worker` to return ``None``."""
    monkeypatch.setattr(worker_module, "_worker", None, raising=False)


@pytest.fixture
def force_capable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``is_image_gen_capable`` to ``(True, CAPABLE, "capable")``."""
    from toybox.image_gen.capability import CapabilityReason

    monkeypatch.setattr(
        toys_router_mod,
        "is_image_gen_capable",
        lambda **_kw: (True, CapabilityReason.capable, "capable"),
    )


@pytest.fixture
def force_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin ``is_image_gen_capable`` to ``(False, ENV_DISABLED, "test-disabled")``.

    Uses ``ENV_DISABLED`` so the F.5-3a hard-off branch fires (409,
    no composite fallback). Tests targeting the new composite-only
    branches override this with their own monkeypatch using
    ``NO_CUDA`` / ``LOW_VRAM`` / ``MISSING_CHECKPOINTS``.
    """
    from toybox.image_gen.capability import CapabilityReason

    monkeypatch.setattr(
        toys_router_mod,
        "is_image_gen_capable",
        lambda **_kw: (False, CapabilityReason.env_disabled, "test-disabled"),
    )


def _seed_toy(db_path: Path, toy_id: str = _SEEDED_TOY_ID) -> None:
    """Insert one canonical toy row directly via SQL."""
    conn = connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO toys (id, display_name, image_path, image_hash, "
                "tags, archived, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                (
                    toy_id,
                    "Sparkle Unicorn",
                    f"data/images/toys/{toy_id}.jpg",
                    f"hash-{toy_id}",
                    "plush,unicorn",
                    "2026-05-06T00:00:00Z",
                ),
            )
    finally:
        conn.close()


def _jpeg_bytes(
    size: tuple[int, int] = (64, 64),
    color: tuple[int, int, int] = (200, 100, 50),
) -> bytes:
    img = Image.new("RGB", size, color)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


def _upload_files(
    payload: bytes,
    filename: str = "toy.jpg",
    mime: str = "image/jpeg",
) -> dict[str, tuple[str, bytes, str]]:
    return {"file": (filename, payload, mime)}


# ---------------------------------------------------------------------
# GET /api/toys/{id}/actions
# ---------------------------------------------------------------------


def test_get_actions_returns_10_rows_for_existing_toy(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    force_capable: None,
) -> None:
    _seed_toy(db_path)
    resp = vc_client.get(
        f"/api/toys/{_SEEDED_TOY_ID}/actions",
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "actions" in body
    assert len(body["actions"]) == 10
    # Order matches ACTION_SLOTS verbatim.
    assert [row["slot"] for row in body["actions"]] == list(ACTION_SLOTS)
    # All synthesized as not_started for a freshly-seeded toy.
    assert all(row["status"] == "not_started" for row in body["actions"])
    assert all(row["image_path"] is None for row in body["actions"])
    assert all(row["toy_id"] == _SEEDED_TOY_ID for row in body["actions"])
    assert body["capability"] == {"capable": True, "reason": "capable"}


def test_get_actions_404_for_unknown_toy(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    force_capable: None,
) -> None:
    unknown = str(uuid.uuid4())
    resp = vc_client.get(f"/api/toys/{unknown}/actions", headers=parent_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "toy_not_found"


def test_get_actions_404_for_invalid_toy_id(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    force_capable: None,
) -> None:
    """A path-traversal attempt returns 404 (not 500/422)."""
    resp = vc_client.get("/api/toys/not-a-uuid/actions", headers=parent_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "toy_not_found"


def test_get_actions_includes_capability_disabled_reason(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    force_disabled: None,
) -> None:
    """GET still returns 200 with rows + capability=False (graceful degradation)."""
    _seed_toy(db_path)
    resp = vc_client.get(f"/api/toys/{_SEEDED_TOY_ID}/actions", headers=parent_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["actions"]) == 10
    assert body["capability"] == {"capable": False, "reason": "test-disabled"}
    # ENV_DISABLED → mode is None (no Tier C fallback).
    assert body.get("mode") is None


@pytest.fixture
def force_composite_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin capability to a Tier-C-eligible False reason."""
    from toybox.image_gen.capability import CapabilityReason

    monkeypatch.setattr(
        toys_router_mod,
        "is_image_gen_capable",
        lambda **_kw: (
            False,
            CapabilityReason.missing_checkpoints,
            "test-missing-checkpoints",
        ),
    )


def test_get_actions_emits_composite_only_mode(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    force_composite_only: None,
) -> None:
    """F.5-3a: capability-False with non-env-disabled reason → mode=composite_only."""
    _seed_toy(db_path)
    resp = vc_client.get(f"/api/toys/{_SEEDED_TOY_ID}/actions", headers=parent_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "composite_only"
    assert body["capability"]["capable"] is False
    assert body["capability"]["reason"] == "test-missing-checkpoints"


def test_get_actions_requires_parent_token(
    vc_client: TestClient,
) -> None:
    resp = vc_client.get(f"/api/toys/{_SEEDED_TOY_ID}/actions")
    assert resp.status_code == 401


def test_get_actions_forbids_child_token(
    vc_client: TestClient,
    child_token: str,
) -> None:
    resp = vc_client.get(
        f"/api/toys/{_SEEDED_TOY_ID}/actions",
        headers={"Authorization": f"Bearer {child_token}"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------
# POST /api/toys/{id}/actions/regenerate
# ---------------------------------------------------------------------


def test_regenerate_all_enqueues_10(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    force_capable: None,
    stub_worker: _StubWorker,
) -> None:
    _seed_toy(db_path)
    resp = vc_client.post(
        f"/api/toys/{_SEEDED_TOY_ID}/actions/regenerate",
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["queued"] == list(ACTION_SLOTS)
    # F.5-3a: capability=CAPABLE → mode is None (no Tier C banner).
    assert body.get("mode") is None
    assert len(stub_worker.enqueued) == 10
    enqueued_slots = [slot for (_, slot, _) in stub_worker.enqueued]
    assert enqueued_slots == list(ACTION_SLOTS)
    assert all(toy_id == _SEEDED_TOY_ID for (toy_id, _, _) in stub_worker.enqueued)


def test_regenerate_all_409_when_disabled(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    force_disabled: None,
    stub_worker: _StubWorker,
) -> None:
    """ENV_DISABLED still returns 409 (regression check for F.5-3a)."""
    _seed_toy(db_path)
    resp = vc_client.post(
        f"/api/toys/{_SEEDED_TOY_ID}/actions/regenerate",
        headers=parent_headers,
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "image_gen_disabled"
    assert detail["reason"] == "test-disabled"
    assert stub_worker.enqueued == []


def test_regenerate_all_200_with_composite_only_mode(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    force_composite_only: None,
    stub_worker: _StubWorker,
) -> None:
    """F.5-3a: capability-False non-env-disabled → 200 + queued + mode field.

    Worker still enqueues all 10 jobs (the worker dispatches to the
    composite path internally); the response carries
    ``mode="composite_only"`` so the parent UI renders the banner.
    """
    _seed_toy(db_path)
    resp = vc_client.post(
        f"/api/toys/{_SEEDED_TOY_ID}/actions/regenerate",
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["queued"] == list(ACTION_SLOTS)
    assert body["mode"] == "composite_only"
    assert len(stub_worker.enqueued) == 10


def test_regenerate_all_404_for_unknown_toy(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    force_capable: None,
    stub_worker: _StubWorker,
) -> None:
    unknown = str(uuid.uuid4())
    resp = vc_client.post(f"/api/toys/{unknown}/actions/regenerate", headers=parent_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "toy_not_found"
    assert stub_worker.enqueued == []


def test_regenerate_all_503_when_worker_not_running(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    force_capable: None,
    no_worker: None,
) -> None:
    _seed_toy(db_path)
    resp = vc_client.post(
        f"/api/toys/{_SEEDED_TOY_ID}/actions/regenerate",
        headers=parent_headers,
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "image_gen_worker_unavailable"


# ---------------------------------------------------------------------
# POST /api/toys/{id}/actions/{slot}/regenerate
# ---------------------------------------------------------------------


def test_regenerate_one_enqueues_single(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    force_capable: None,
    stub_worker: _StubWorker,
) -> None:
    _seed_toy(db_path)
    resp = vc_client.post(
        f"/api/toys/{_SEEDED_TOY_ID}/actions/jumping/regenerate",
        headers=parent_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["queued"] == ["jumping"]
    assert body.get("mode") is None
    assert len(stub_worker.enqueued) == 1
    toy_id, slot, _seed = stub_worker.enqueued[0]
    assert toy_id == _SEEDED_TOY_ID
    assert slot == "jumping"


def test_regenerate_one_404_for_unknown_slot(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    force_capable: None,
    stub_worker: _StubWorker,
) -> None:
    _seed_toy(db_path)
    resp = vc_client.post(
        f"/api/toys/{_SEEDED_TOY_ID}/actions/banana/regenerate",
        headers=parent_headers,
    )
    assert resp.status_code == 404, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "slot_not_in_vocab"
    assert detail["slot"] == "banana"
    assert stub_worker.enqueued == []


def test_regenerate_one_404_for_unknown_toy(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    force_capable: None,
    stub_worker: _StubWorker,
) -> None:
    unknown = str(uuid.uuid4())
    resp = vc_client.post(
        f"/api/toys/{unknown}/actions/idle/regenerate",
        headers=parent_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "toy_not_found"
    assert stub_worker.enqueued == []


def test_regenerate_one_409_when_disabled(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    force_disabled: None,
    stub_worker: _StubWorker,
) -> None:
    """ENV_DISABLED still returns 409 for the per-slot endpoint."""
    _seed_toy(db_path)
    resp = vc_client.post(
        f"/api/toys/{_SEEDED_TOY_ID}/actions/idle/regenerate",
        headers=parent_headers,
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "image_gen_disabled"
    assert detail["reason"] == "test-disabled"
    assert stub_worker.enqueued == []


def test_regenerate_one_503_when_worker_not_running(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    db_path: Path,
    force_capable: None,
    no_worker: None,
) -> None:
    _seed_toy(db_path)
    resp = vc_client.post(
        f"/api/toys/{_SEEDED_TOY_ID}/actions/idle/regenerate",
        headers=parent_headers,
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "image_gen_worker_unavailable"


# ---------------------------------------------------------------------
# POST /api/toys commit hook — the non-negotiable integration test
# ---------------------------------------------------------------------


def _upload_and_get_staging_id(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    color: tuple[int, int, int] = (200, 100, 50),
) -> str:
    upload_resp = vc_client.post(
        "/api/toys/upload",
        files=_upload_files(_jpeg_bytes(color=color)),
        headers=parent_headers,
    )
    assert upload_resp.status_code == 200, upload_resp.text
    return str(upload_resp.json()["staging_id"])


def test_toy_commit_hook_enqueues_10_jobs(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    force_capable: None,
    stub_worker: _StubWorker,
) -> None:
    """Drives ``POST /api/toys`` end-to-end and asserts 10 jobs were enqueued.

    This is the integration test through the production handler
    required by the ``feedback_buildstep_require_integration_test.md``
    memory: NOT a unit test of the helper, but a full HTTP round-trip
    that exercises the staging → commit → enqueue chain.
    """
    staging_id = _upload_and_get_staging_id(vc_client, parent_headers)
    confirm = vc_client.post(
        "/api/toys",
        json={
            "staging_id": staging_id,
            "display_name": "Sparkle Unicorn",
            "tags": ["plush", "unicorn"],
            "persona_id": None,
        },
        headers=parent_headers,
    )
    assert confirm.status_code == 201, confirm.text
    new_toy_id = confirm.json()["id"]

    # Hook must have enqueued exactly 10 jobs, one per ACTION_SLOTS.
    assert len(stub_worker.enqueued) == 10
    enqueued_slots = [slot for (_, slot, _) in stub_worker.enqueued]
    assert enqueued_slots == list(ACTION_SLOTS)
    assert all(toy_id == new_toy_id for (toy_id, _, _) in stub_worker.enqueued)


def test_toy_commit_hook_enqueues_when_composite_only(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    force_composite_only: None,
    stub_worker: _StubWorker,
) -> None:
    """F.5-3a: composite-only capability still enqueues all 10 jobs.

    The worker dispatches each job to the Tier C path internally; the
    REST layer's commit hook just enqueues. Only ENV_DISABLED skips.
    """
    staging_id = _upload_and_get_staging_id(vc_client, parent_headers, color=(70, 80, 90))
    confirm = vc_client.post(
        "/api/toys",
        json={
            "staging_id": staging_id,
            "display_name": "Composite Toy",
            "tags": [],
            "persona_id": None,
        },
        headers=parent_headers,
    )
    assert confirm.status_code == 201, confirm.text
    assert len(stub_worker.enqueued) == 10


def test_toy_commit_hook_skips_enqueue_when_disabled(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    force_disabled: None,
    stub_worker: _StubWorker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ENV_DISABLED still skips the commit-hook enqueue; INFO logged."""
    staging_id = _upload_and_get_staging_id(vc_client, parent_headers, color=(50, 60, 70))
    with caplog.at_level(logging.INFO, logger="toybox.api.toys"):
        confirm = vc_client.post(
            "/api/toys",
            json={
                "staging_id": staging_id,
                "display_name": "Skip Toy",
                "tags": [],
                "persona_id": None,
            },
            headers=parent_headers,
        )
    assert confirm.status_code == 201, confirm.text
    assert stub_worker.enqueued == []
    # Log should mention "skipping enqueue" + the reason.
    matching = [rec for rec in caplog.records if "skipping enqueue" in rec.getMessage()]
    assert matching, f"expected a 'skipping enqueue' log; got {caplog.text!r}"
    assert any("test-disabled" in rec.getMessage() for rec in matching)


def test_toy_commit_hook_skips_when_worker_not_running(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    force_capable: None,
    no_worker: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Toy create still succeeds when the worker isn't started."""
    staging_id = _upload_and_get_staging_id(vc_client, parent_headers, color=(80, 90, 100))
    with caplog.at_level(logging.INFO, logger="toybox.api.toys"):
        confirm = vc_client.post(
            "/api/toys",
            json={
                "staging_id": staging_id,
                "display_name": "No-Worker Toy",
                "tags": [],
                "persona_id": None,
            },
            headers=parent_headers,
        )
    assert confirm.status_code == 201, confirm.text
    matching = [rec for rec in caplog.records if "image-gen worker not running" in rec.getMessage()]
    assert matching, f"expected worker-not-running log; got {caplog.text!r}"


def test_toy_commit_hook_logs_warning_on_enqueue_failure(
    vc_client: TestClient,
    parent_headers: dict[str, str],
    force_capable: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A worker.enqueue exception must NOT break the toy create."""
    boom = _StubWorker(raise_exc=RuntimeError("queue is wedged"))
    monkeypatch.setattr(worker_module, "_worker", boom, raising=False)

    staging_id = _upload_and_get_staging_id(vc_client, parent_headers, color=(110, 120, 130))
    with caplog.at_level(logging.WARNING, logger="toybox.api.toys"):
        confirm = vc_client.post(
            "/api/toys",
            json={
                "staging_id": staging_id,
                "display_name": "Boom Toy",
                "tags": [],
                "persona_id": None,
            },
            headers=parent_headers,
        )
    assert confirm.status_code == 201, confirm.text
    matching = [rec for rec in caplog.records if "enqueue failed" in rec.getMessage()]
    assert matching, f"expected enqueue-failed warning; got {caplog.text!r}"
    assert any(rec.levelno == logging.WARNING for rec in matching)


# ---------------------------------------------------------------------
# Auth gates (parent-only)
# ---------------------------------------------------------------------


_NEW_PROTECTED_ENDPOINTS: list[tuple[str, str]] = [
    ("GET", f"/api/toys/{_SEEDED_TOY_ID}/actions"),
    ("POST", f"/api/toys/{_SEEDED_TOY_ID}/actions/regenerate"),
    ("POST", f"/api/toys/{_SEEDED_TOY_ID}/actions/idle/regenerate"),
]


@pytest.mark.parametrize(("method", "path"), _NEW_PROTECTED_ENDPOINTS)
def test_new_endpoints_require_parent_token(
    vc_client: TestClient,
    method: str,
    path: str,
) -> None:
    resp = vc_client.request(method, path)
    assert resp.status_code == 401


@pytest.mark.parametrize(("method", "path"), _NEW_PROTECTED_ENDPOINTS)
def test_new_endpoints_forbid_child_token(
    vc_client: TestClient,
    child_token: str,
    method: str,
    path: str,
) -> None:
    headers = {"Authorization": f"Bearer {child_token}"}
    resp = vc_client.request(method, path, headers=headers)
    assert resp.status_code == 403
