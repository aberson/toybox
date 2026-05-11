"""Smoke coverage for the production-lifespan composable helpers.

The production lifespan in :func:`toybox.main._metrics_lifespan` is too
heavyweight to drive in a unit test — it starts a live mic, loads
Whisper, and spawns the metrics publisher. Each composable lifespan
helper, on the other hand, is exercisable on its own with a plain
``FastAPI()`` and an ``async with`` block.

Phase I Step I2 added :func:`transcript_sweep_lifespan`; this test is
the primary smoke for that helper. It asserts:

* ``async with transcript_sweep_lifespan(app)`` enters cleanly,
* the spawned task is a real :class:`asyncio.Task`,
* the task is not done before exit,
* the helper swallows the ``CancelledError`` raised on exit so the
  ``async with`` returns without re-raising.

Replaces the brittle inline ``python -c`` subprocess smoke that earlier
phases used — a real pytest is easier to debug and tracks the
asyncio.Task contract directly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI

from toybox.app import transcript_sweep_lifespan


@pytest.fixture
def configured_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``TOYBOX_DB_PATH`` at a fresh per-test DB so the sweep
    task's own ``conn_factory`` opens a real file (the migration runner
    will create the schema on first connect)."""
    db_path = tmp_path / "toybox.db"
    monkeypatch.setenv("TOYBOX_DB_PATH", str(db_path))
    # Migrate so the sweep loop's first tick (10s after enter, which
    # this test never reaches) wouldn't error out if it ran. The
    # primary assertion is enter/exit lifecycle so the schema is just
    # defensive.
    from toybox.db.connection import connect
    from toybox.db.migrations import run_migrations

    conn = connect(db_path)
    try:
        run_migrations(conn)
    finally:
        conn.close()
    return db_path


async def test_transcript_sweep_lifespan_clean_startup_and_shutdown(
    configured_paths: Path,
) -> None:
    """The lifespan must enter, yield a running task, and exit cleanly.

    The sweep loop's default interval is 10s, so the task is parked in
    ``asyncio.sleep`` for the full duration of the ``async with`` body;
    no tick will fire during the brief 100ms hold. The shutdown path
    cancels the task and the lifespan's
    ``except asyncio.CancelledError: pass`` clause keeps the exception
    from escaping.
    """
    app = FastAPI()
    async with transcript_sweep_lifespan(app) as task:
        assert isinstance(task, asyncio.Task)
        assert not task.done()
        # Brief hold to verify the task continues running while the
        # lifespan body is active.
        await asyncio.sleep(0.1)
        assert not task.done()
    # After exit: task was cancelled and awaited; the lifespan
    # swallowed CancelledError so we just need to confirm the task is
    # done. ``task.cancelled()`` would be True for a cleanly-cancelled
    # task that never started its body.
    assert task.done()
