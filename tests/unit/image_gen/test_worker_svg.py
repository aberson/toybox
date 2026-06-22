"""Worker integration coverage for the Claude-Images SVG path.

Pins the ``image_gen_mode == "claude_svg"`` branch in
:meth:`ImageGenWorker._run_one_body` → :meth:`_run_one_svg`: when the
mode is ``claude_svg`` the worker generates an SVG (no SD
capability/breaker gates), writes ``<slot>.svg``, removes sibling
formats, and commits ``done`` with an ``.svg`` ``image_path``. Failure
modes (no token, malformed reply) mark the row ``failed`` so the kiosk
falls back. The mode is mutually exclusive with cartoon/composite — a
``claude_svg`` job never reaches the SD pipeline.

Mirrors the harness in :mod:`tests.unit.image_gen.test_worker_mode_dispatch`.
The raising-mode-probe path is covered there
(``test_mode_probe_raise_marks_row_failed``), not duplicated here.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.image_gen.capability import (
    CapabilityReason,
    reset_image_gen_breaker_for_tests,
)
from toybox.image_gen.models import GenerationContext
from toybox.image_gen.svg_gen import ClaudeImagesUnavailable, SvgRateLimitedError
from toybox.image_gen.worker import ImageGenWorker
from toybox.ws.topics import Topic

_TOY_ID = "550e8400-e29b-41d4-a716-446655440000"
_TOY_PHOTO_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"


@pytest.fixture(autouse=True)
def _reset_breaker() -> None:
    reset_image_gen_breaker_for_tests()
    yield
    reset_image_gen_breaker_for_tests()


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
                    "Bagheera",
                    "data/images/toys/bagheera.jpg",
                    "h1",
                    "2026-06-21T00:00:00Z",
                ),
            )
    finally:
        conn.close()
    photo = tmp_path / "images" / "toys" / "bagheera.jpg"
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


def _svg_stub(
    log: list[tuple[str, GenerationContext]],
    *,
    svg: str = '<svg viewBox="0 0 128 128"><rect/></svg>',
) -> Callable[[bytes, str, GenerationContext], Awaitable[str]]:
    async def _gen(_b: bytes, slot: str, ctx: GenerationContext) -> str:
        log.append((slot, ctx))
        return svg

    return _gen


def _pipeline_stub(
    log: list[tuple[str, int]],
) -> Callable[[bytes, str, int, GenerationContext], Awaitable[bytes]]:
    async def _pipe(_b: bytes, slot: str, seed: int, _ctx: GenerationContext) -> bytes:
        log.append((slot, seed))
        return b"\x89PNG\r\n\x1a\nPIPELINE"

    return _pipe


async def _drive_one_job(
    worker: ImageGenWorker,
    captured: list[tuple[Topic, dict[str, object]]],
    *,
    slot: str = "idle",
    seed: int,
    terminal_statuses: tuple[str, ...] = ("done", "failed"),
) -> None:
    await worker.start()
    try:
        await worker.enqueue(_TOY_ID, slot, seed=seed)
        for _ in range(300):
            await asyncio.sleep(0.01)
            if any(p["status"] in terminal_statuses for _, p in captured):
                break
    finally:
        await worker.stop()


def _row_image_path(db_path: Path, slot: str) -> str | None:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT image_path FROM toy_actions WHERE toy_id = ? AND slot = ?",
            (_TOY_ID, slot),
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else row["image_path"]


async def test_claude_svg_mode_writes_svg_and_commits_done(db_path: Path, tmp_path: Path) -> None:
    captured, emit = _capture_emit()
    gen_log: list[tuple[str, GenerationContext]] = []

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        mode_probe=lambda: "claude_svg",
        svg_generator=_svg_stub(gen_log),
    )
    await _drive_one_job(worker, captured, seed=1)

    # Generator ran for the idle slot.
    assert [s for s, _ in gen_log] == ["idle"]
    # SVG written to disk.
    out = tmp_path / "images" / "toy_actions" / _TOY_ID / "idle.svg"
    assert out.is_file()
    assert out.read_text(encoding="utf-8").startswith("<svg")
    # done envelope + DB row carry the .svg image_path.
    done = [p for _, p in captured if p["status"] == "done"]
    assert len(done) == 1
    assert str(done[0]["image_path"]).endswith("/idle.svg")
    assert str(_row_image_path(db_path, "idle")).endswith("/idle.svg")


async def test_svg_path_removes_stale_png_sibling(db_path: Path, tmp_path: Path) -> None:
    # Pre-existing PNG from a prior SD generation.
    out_dir = tmp_path / "images" / "toy_actions" / _TOY_ID
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "idle.png").write_bytes(b"\x89PNG\r\n\x1a\nOLD")

    captured, emit = _capture_emit()
    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        mode_probe=lambda: "claude_svg",
        svg_generator=_svg_stub([]),
    )
    await _drive_one_job(worker, captured, seed=2)

    assert (out_dir / "idle.svg").is_file()
    assert not (out_dir / "idle.png").exists()


async def test_generator_unavailable_marks_failed(db_path: Path) -> None:
    captured, emit = _capture_emit()

    async def _unavailable(_b: bytes, _slot: str, _ctx: GenerationContext) -> str:
        raise ClaudeImagesUnavailable("no token")

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        mode_probe=lambda: "claude_svg",
        svg_generator=_unavailable,
    )
    await _drive_one_job(worker, captured, seed=3, terminal_statuses=("failed",))

    failed = [p for _, p in captured if p["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["error"] == "claude_images_unavailable"


async def test_generator_rate_limited_marks_failed_cleanly(db_path: Path) -> None:
    captured, emit = _capture_emit()

    async def _rate_limited(_b: bytes, _slot: str, _ctx: GenerationContext) -> str:
        raise SvgRateLimitedError("Claude rate limit (HTTP 429) after 3 attempts")

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        mode_probe=lambda: "claude_svg",
        svg_generator=_rate_limited,
    )
    await _drive_one_job(worker, captured, seed=4, terminal_statuses=("failed",))

    failed = [p for _, p in captured if p["status"] == "failed"]
    assert len(failed) == 1
    # Clean operator-readable reason, not a raw "HTTP Error 429" string.
    assert failed[0]["error"] == "claude_images_rate_limited"


async def test_cartoon_mode_routes_to_sd_pipeline(db_path: Path, tmp_path: Path) -> None:
    """mode=cartoon → SD pipeline runs; the SVG generator is never called."""
    captured, emit = _capture_emit()
    gen_log: list[tuple[str, GenerationContext]] = []
    pipe_log: list[tuple[str, int]] = []

    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        pipeline=_pipeline_stub(pipe_log),
        capability_probe=lambda: (True, CapabilityReason.capable, "ok"),
        mode_probe=lambda: "cartoon",
        svg_generator=_svg_stub(gen_log),
    )
    await _drive_one_job(worker, captured, seed=5)

    assert gen_log == []
    assert pipe_log == [("idle", 5)]
    out = tmp_path / "images" / "toy_actions" / _TOY_ID / "idle.png"
    assert out.is_file()


async def test_default_probe_reads_claude_svg_mode_from_db(db_path: Path, tmp_path: Path) -> None:
    """With no mode_probe override, the worker reads settings.image_gen_mode."""
    from toybox.core.image_gen_mode import set_image_gen_mode

    conn = connect(db_path)
    try:
        set_image_gen_mode(conn, "claude_svg")
    finally:
        conn.close()

    captured, emit = _capture_emit()
    gen_log: list[tuple[str, GenerationContext]] = []
    worker = ImageGenWorker(
        _conn_factory(db_path),
        emit,
        svg_generator=_svg_stub(gen_log),
    )
    await _drive_one_job(worker, captured, seed=6)

    assert [s for s, _ in gen_log] == ["idle"]
    out = tmp_path / "images" / "toy_actions" / _TOY_ID / "idle.svg"
    assert out.is_file()
