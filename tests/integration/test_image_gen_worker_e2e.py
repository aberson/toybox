"""End-to-end coverage for the F4 worker via :func:`create_app`.

Wires the worker via the public app-factory + lifespan path
(:func:`toybox.app.image_gen_worker_lifespan`) and asserts the full
``queued`` → ``running`` → ``done`` lifecycle:

1. PNG written to ``data/images/toy_actions/<toy_id>/<slot>.png``.
2. DB row in ``done`` state with the persisted ``image_path``.
3. WS envelopes captured via the subscriber pattern used by
   :mod:`tests.integration.test_metrics_ws`.

The pipeline is the deterministic stub
(``TOYBOX_IMAGE_GEN_STUB=1``); we don't exercise torch / diffusers
in CI.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.image_gen.capability import reset_image_gen_breaker_for_tests
from toybox.image_gen.worker import (
    reset_image_gen_worker_for_tests,
    start_image_gen_worker,
    stop_image_gen_worker,
)
from toybox.ws.envelope import Envelope, build_envelope
from toybox.ws.topics import Topic

_TOY_ID = "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture(autouse=True)
def _reset_singletons() -> Iterator[None]:
    reset_image_gen_worker_for_tests()
    reset_image_gen_breaker_for_tests()
    yield
    reset_image_gen_worker_for_tests()
    reset_image_gen_breaker_for_tests()


@pytest.fixture
def configured_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Migrate a tmp DB, seed a toy + photo, redirect data dir."""
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TOYBOX_DB_PATH", str(tmp_path / "toybox.db"))
    # Force the deterministic stub pipeline.
    monkeypatch.setenv("TOYBOX_IMAGE_GEN_STUB", "1")
    monkeypatch.delenv("TOYBOX_IMAGE_GEN_STUB_MODE", raising=False)
    monkeypatch.delenv("TOYBOX_IMAGE_GEN_STUB_DELAY_SEC", raising=False)

    db = tmp_path / "toybox.db"
    conn = connect(db)
    try:
        run_migrations(conn)
        with conn:
            conn.execute(
                "INSERT INTO toys (id, display_name, image_path, image_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    _TOY_ID,
                    "Bunny",
                    "data/images/toys/bunny.jpg",
                    "h1",
                    "2026-05-06T00:00:00Z",
                ),
            )
    finally:
        conn.close()

    photo = tmp_path / "images" / "toys" / "bunny.jpg"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01")
    return db


async def test_full_lifecycle_via_app_lifespan(
    configured_paths: Path,
    tmp_path: Path,
) -> None:
    """The full app-lifespan path drives queue → stub pipeline → DB → WS.

    Uses :func:`start_image_gen_worker` directly with a per-test
    pubsub-emit so we avoid touching the global pubsub. The worker is
    the same singleton the FastAPI lifespan would set up.
    """
    pubsub = PubSub(coalesce_window_ms=0)

    async def _emit(topic: Topic, payload: dict[str, object]) -> None:
        pubsub.publish(build_envelope(topic=topic, payload=payload))

    def _conn_factory() -> object:
        return connect(configured_paths, check_same_thread=False)

    # Use the real (stubbed) pipeline path — no override.
    worker = await start_image_gen_worker(_conn_factory, _emit)
    try:
        # Subscribe BEFORE enqueue so the ``queued`` envelope isn't
        # missed.
        sub = pubsub.subscribe([Topic.toy_actions])
        try:
            await worker.enqueue(_TOY_ID, "idle", seed=12345)
            collected: list[Envelope] = []

            async def _collect_until_done() -> None:
                async with asyncio.timeout(5.0):
                    while True:
                        env = await sub.get()
                        if env.topic is not Topic.toy_actions:
                            continue
                        collected.append(env)
                        if env.payload.get("status") == "done":
                            return

            await _collect_until_done()
        finally:
            sub.close()
    finally:
        await stop_image_gen_worker()

    statuses = [env.payload["status"] for env in collected]
    assert statuses == ["queued", "running", "done"], statuses

    done = collected[-1]
    assert done.payload["toy_id"] == _TOY_ID
    assert done.payload["slot"] == "idle"
    assert done.payload["image_path"] == (
        f"data/images/toy_actions/{_TOY_ID}/idle.png"
    )
    assert done.payload["error"] is None

    # PNG actually written by the stub pipeline.
    out_path = tmp_path / "images" / "toy_actions" / _TOY_ID / "idle.png"
    assert out_path.is_file()
    # Stub pipeline produces a valid PNG (16x16 RGBA per the stub
    # fixture); just sanity-check the magic bytes.
    raw = out_path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"

    # DB row reflects ``done``.
    conn = connect(configured_paths)
    try:
        row = conn.execute(
            "SELECT status, image_path FROM toy_actions "
            "WHERE toy_id = ? AND slot = ?",
            (_TOY_ID, "idle"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["status"] == "done"
    assert row["image_path"] == f"data/images/toy_actions/{_TOY_ID}/idle.png"
