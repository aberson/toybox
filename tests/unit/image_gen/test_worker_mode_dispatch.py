"""Pin the F.5-toggle mode dispatch matrix.

The operator's ``settings.image_gen_mode`` toggle interacts with
:func:`toybox.image_gen.capability.is_image_gen_capable` per the brief:

================  ================  =====================
mode              capable           dispatch
================  ================  =====================
cartoon           True              Tier B pipeline
cartoon           False             Tier C composite
composite         True              Tier C composite (NEW)
composite         False             Tier C composite
*                 env_disabled      hard-off (no composite)
================  ================  =====================

Each cell is one test below; ``mode_probe`` and ``capability_probe`` are
threaded through the worker constructor so the test pins the branch
without poking the DB or env vars.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import Mock

import pytest

from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.image_gen.capability import (
    CapabilityReason,
    reset_image_gen_breaker_for_tests,
)
from toybox.image_gen.models import GenerationContext, ToyActionStatus
from toybox.image_gen.worker import ImageGenWorker
from toybox.ws.topics import Topic

_TOY_ID = "550e8400-e29b-41d4-a716-446655440000"
_TOY_PHOTO_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"


@pytest.fixture(autouse=True)
def _reset_breaker() -> None:
    reset_image_gen_breaker_for_tests()
    yield
    reset_image_gen_breaker_for_tests()


def _capable_probe() -> tuple[bool, CapabilityReason, str]:
    return True, CapabilityReason.capable, "capable"


def _incapable_probe() -> tuple[bool, CapabilityReason, str]:
    return False, CapabilityReason.missing_checkpoints, "test-missing"


def _env_disabled_probe() -> tuple[bool, CapabilityReason, str]:
    return False, CapabilityReason.env_disabled, "test-env-disabled"


# ---------------------------------------------------------------------
# Module-level pipeline + composite stubs.
#
# Each test case wants to assert which branch ran; the stubs append the
# (slot, seed) pair to a per-test list passed in. Hoisting the stub
# factories here avoids verbatim re-declaring them inside every test.
# ---------------------------------------------------------------------


def _build_pipeline_stub(
    log: list[tuple[str, int]],
) -> Callable[[bytes, str, int, GenerationContext], Awaitable[bytes]]:
    async def _pipeline_stub(
        _b: bytes, slot: str, seed: int, _ctx: GenerationContext
    ) -> bytes:
        log.append((slot, seed))
        return b"\x89PNG\r\n\x1a\nPIPELINE"

    return _pipeline_stub


def _build_composite_stub(
    log: list[tuple[str, int]],
) -> Callable[[bytes, str, int, GenerationContext], Awaitable[bytes]]:
    async def _composite_stub(
        _b: bytes, slot: str, seed: int, _ctx: GenerationContext
    ) -> bytes:
        log.append((slot, seed))
        return b"\x89PNG\r\n\x1a\nCOMPOSITE"

    return _composite_stub


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TOYBOX_DATA_DIR", str(tmp_path))
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
                    "2026-05-09T00:00:00Z",
                ),
            )
    finally:
        conn.close()
    photo = tmp_path / "images" / "toys" / "bunny.jpg"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(_TOY_PHOTO_BYTES)
    return db


def _conn_factory(db_path: Path) -> Callable[[], sqlite3.Connection]:
    def _factory() -> sqlite3.Connection:
        return connect(db_path, check_same_thread=False)

    return _factory


def _capture_emit() -> tuple[
    list[tuple[Topic, dict[str, object]]],
    Callable[[Topic, dict[str, object]], Awaitable[None]],
]:
    captured: list[tuple[Topic, dict[str, object]]] = []

    async def _emit(topic: Topic, payload: dict[str, object]) -> None:
        captured.append((topic, payload))

    return captured, _emit


async def _drive_one_job(
    worker: ImageGenWorker,
    captured: list[tuple[Topic, dict[str, object]]],
    *,
    seed: int,
    terminal_statuses: tuple[str, ...] = ("done", "failed"),
) -> None:
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, "idle", seed=seed)
        for _ in range(300):
            await asyncio.sleep(0.01)
            if any(p["status"] in terminal_statuses for _, p in captured):
                break
    finally:
        await worker.stop()


async def test_cartoon_capable_routes_to_pipeline(db_path: Path) -> None:
    # Arrange — cartoon mode + capable host → Tier B.
    captured, emit = _capture_emit()
    pipeline_calls: list[tuple[str, int]] = []
    composite_calls: list[tuple[str, int]] = []

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        pipeline=_build_pipeline_stub(pipeline_calls),
        composite=_build_composite_stub(composite_calls),
        capability_probe=_capable_probe,
        mode_probe=lambda: "cartoon",
    )

    # Act
    await _drive_one_job(worker, captured, seed=1)

    # Assert
    assert pipeline_calls == [("idle", 1)]
    assert composite_calls == []
    done = [p for _, p in captured if p["status"] == "done"]
    assert len(done) == 1


async def test_cartoon_incapable_routes_to_composite(db_path: Path) -> None:
    # Arrange — cartoon mode + incapable host → Tier C (existing fallback).
    # NOTE: this case stands in for the "composite-incapable" cell of the
    # dispatch matrix too — at the dispatch boundary both rows execute
    # the same composite path. Don't add a separate composite-incapable
    # test; it would just rerun this code with a different mode_probe.
    captured, emit = _capture_emit()
    pipeline_calls: list[tuple[str, int]] = []
    composite_calls: list[tuple[str, int]] = []

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        pipeline=_build_pipeline_stub(pipeline_calls),
        composite=_build_composite_stub(composite_calls),
        capability_probe=_incapable_probe,
        mode_probe=lambda: "cartoon",
    )

    # Act
    await _drive_one_job(worker, captured, seed=2)

    # Assert
    assert pipeline_calls == []
    assert composite_calls == [("idle", 2)]


async def test_composite_capable_forces_composite(db_path: Path) -> None:
    # Arrange — composite mode + CAPABLE host → Tier C (forced override).
    captured, emit = _capture_emit()
    pipeline_calls: list[tuple[str, int]] = []
    composite_calls: list[tuple[str, int]] = []

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        pipeline=_build_pipeline_stub(pipeline_calls),
        composite=_build_composite_stub(composite_calls),
        capability_probe=_capable_probe,
        mode_probe=lambda: "composite",
    )

    # Act
    await _drive_one_job(worker, captured, seed=3)

    # Assert — composite ran, pipeline did NOT, even though capable=True.
    assert pipeline_calls == []
    assert composite_calls == [("idle", 3)]
    done = [p for _, p in captured if p["status"] == "done"]
    assert len(done) == 1


@pytest.mark.parametrize("mode", ["cartoon", "composite"])
async def test_env_disabled_hard_off_regardless_of_mode(
    db_path: Path,
    mode: str,
) -> None:
    # Arrange — env_disabled wins for both mode values.
    captured, emit = _capture_emit()
    pipeline_calls: list[tuple[str, int]] = []
    composite_calls: list[tuple[str, int]] = []

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        pipeline=_build_pipeline_stub(pipeline_calls),
        composite=_build_composite_stub(composite_calls),
        capability_probe=_env_disabled_probe,
        mode_probe=lambda: mode,
    )

    # Act
    await _drive_one_job(worker, captured, seed=5, terminal_statuses=("failed",))

    # Assert — neither pipeline nor composite ran; row failed with image_gen_disabled.
    assert pipeline_calls == []
    assert composite_calls == []
    failed = [p for _, p in captured if p["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["error"] == "image_gen_disabled"


async def test_default_mode_probe_reads_from_db(db_path: Path) -> None:
    """When ``mode_probe`` is None, the worker reads ``settings.image_gen_mode``."""
    # Arrange — flip the persisted setting to ``composite``; pass NO mode_probe.
    from toybox.core.image_gen_mode import set_image_gen_mode

    conn = connect(db_path)
    try:
        set_image_gen_mode(conn, "composite")
    finally:
        conn.close()

    captured, emit = _capture_emit()
    pipeline_calls: list[tuple[str, int]] = []
    composite_calls: list[tuple[str, int]] = []

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        pipeline=_build_pipeline_stub(pipeline_calls),
        composite=_build_composite_stub(composite_calls),
        capability_probe=_capable_probe,
    )

    # Act
    await _drive_one_job(worker, captured, seed=6)

    # Assert — DB-resolved mode flipped dispatch to composite.
    assert pipeline_calls == []
    assert composite_calls == [("idle", 6)]


async def test_mode_read_per_job_no_restart_required(db_path: Path) -> None:
    """Two consecutive jobs see different modes — proves per-job freshness."""
    # Arrange
    captured, emit = _capture_emit()
    pipeline_calls: list[tuple[str, int]] = []
    composite_calls: list[tuple[str, int]] = []
    mode_log: list[str] = []

    def _mutable_mode_probe() -> str:
        mode_log.append(_state["mode"])
        return _state["mode"]

    _state: dict[str, str] = {"mode": "cartoon"}

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        pipeline=_build_pipeline_stub(pipeline_calls),
        composite=_build_composite_stub(composite_calls),
        capability_probe=_capable_probe,
        mode_probe=_mutable_mode_probe,
    )

    # Act — first job under cartoon, then flip to composite, second job.
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, "idle", seed=10)
        for _ in range(300):
            await asyncio.sleep(0.01)
            if any(
                p["status"] == ToyActionStatus.done.value
                for _, p in captured
            ):
                break

        _state["mode"] = "composite"
        captured.clear()
        await worker.enqueue(_TOY_ID, "waving", seed=11)
        for _ in range(300):
            await asyncio.sleep(0.01)
            if any(
                p["status"] == ToyActionStatus.done.value
                for _, p in captured
            ):
                break
    finally:
        await worker.stop()

    # Assert — first job hit pipeline (cartoon), second hit composite.
    assert pipeline_calls == [("idle", 10)]
    assert composite_calls == [("waving", 11)]


async def test_mode_probe_raise_marks_row_failed(db_path: Path) -> None:
    """Regression: a raising ``mode_probe`` must mark the row failed.

    Before the fix, ``_probe_mode`` was called synchronously without a
    try/except, so an ``OperationalError`` (e.g. "database is locked")
    bubbled out of the consumer's job body and the toy_action row was
    left stuck in ``queued`` until the next restart-recovery sweep.
    The fix mirrors the capability-probe pattern: catch + ``_mark_failed``
    with ``error="image_gen_mode_probe_failed"`` and continue running.
    """
    # Arrange — mode_probe raises sqlite3.OperationalError on call.
    captured, emit = _capture_emit()
    pipeline_calls: list[tuple[str, int]] = []
    composite_calls: list[tuple[str, int]] = []
    mode_probe = Mock(side_effect=sqlite3.OperationalError("database is locked"))

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        pipeline=_build_pipeline_stub(pipeline_calls),
        composite=_build_composite_stub(composite_calls),
        capability_probe=_capable_probe,
        mode_probe=mode_probe,
    )

    # Act
    await _drive_one_job(worker, captured, seed=99, terminal_statuses=("failed",))

    # Assert — row ended in ``failed`` with the documented error code,
    # neither dispatch branch ran, and the worker's consumer task is
    # still alive (we drove a full enqueue → start → stop cycle without
    # an unhandled exception bubbling out).
    assert pipeline_calls == []
    assert composite_calls == []
    failed = [p for _, p in captured if p["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["error"] == "image_gen_mode_probe_failed"
    assert mode_probe.called
