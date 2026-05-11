"""Phase I Step I2 — coverage for the transcript sweep helper + loop driver.

Two units under test:

* :func:`sweep_expired_transcripts` — pure synchronous helper that
  reads the retention preset, computes a pipeline-format cutoff, and
  runs one bulk ``DELETE FROM transcripts``. Cases (a-e) below pin
  every branch with controllable ``now``.
* :func:`run_transcript_sweep_loop` — async driver that wakes on a
  cadence and tolerates per-tick exceptions. Case (f) wires a faulty
  ``conn.execute`` to prove the loop keeps running after an error
  tick.

**Fixture timestamp shape** — every transcript row is inserted with
``ended_at`` formatted via :func:`_format_ended_at_cutoff`, the same
private helper the production pipeline and sweep both use. Hand-crafted
ISO strings with ``+00:00`` suffix or microseconds would mismatch on
lexicographic comparison and silently break the assertion.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from toybox.core.transcript_retention import (
    _format_ended_at_cutoff,
    run_transcript_sweep_loop,
    set_retention_seconds,
    sweep_expired_transcripts,
)
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations

# ---------------------------------------------------------------------
# Per-test DB fixture (no FastAPI; the sweep is a pure DB helper)
# ---------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh migrated SQLite file for this test."""
    path = tmp_path / "toybox.db"
    conn = connect(path)
    try:
        run_migrations(conn)
    finally:
        conn.close()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Single connection scoped to the test body.

    The sweep itself opens + closes its own connection in the loop
    driver — this fixture is for the direct-call helper tests.
    """
    connection = connect(db_path, check_same_thread=False)
    try:
        yield connection
    finally:
        connection.close()


def _seed_session(conn: sqlite3.Connection, session_id: str = "s1") -> None:
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            (session_id, "2026-01-01T00:00:00Z"),
        )


def _insert_transcript(
    conn: sqlite3.Connection,
    *,
    row_id: str,
    ended_at: str | None,
    session_id: str = "s1",
    started_at: str | None = None,
) -> None:
    """Insert a transcript row using pipeline-matched ISO strings.

    The plan pins the fixture format to whatever
    :func:`_format_ended_at_cutoff` emits — callers must pre-format
    their datetime via that helper before passing ``ended_at`` in.
    """
    started = started_at if started_at is not None else (
        ended_at if ended_at is not None else _format_ended_at_cutoff(datetime.now(UTC))
    )
    with conn:
        conn.execute(
            "INSERT INTO transcripts "
            "(id, session_id, mic_id, started_at, ended_at, text, "
            " confidence, language) "
            "VALUES (?, ?, NULL, ?, ?, ?, ?, ?)",
            (row_id, session_id, started, ended_at, f"row {row_id}", 0.7, "en"),
        )


# ---------------------------------------------------------------------
# Case (a) — all rows past retention → all deleted
# ---------------------------------------------------------------------


def test_sweep_deletes_all_rows_past_cutoff(conn: sqlite3.Connection) -> None:
    _seed_session(conn)
    base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    # Default retention = 60s. Three rows all 120-300s old → past cutoff.
    for i, offset in enumerate((120, 180, 300)):
        _insert_transcript(
            conn,
            row_id=f"t-{i}",
            ended_at=_format_ended_at_cutoff(base - timedelta(seconds=offset)),
        )

    deleted = sweep_expired_transcripts(conn, base)

    assert deleted == 3
    remaining = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
    assert remaining == 0


# ---------------------------------------------------------------------
# Case (b) — mix of older + newer rows → only older deleted
# ---------------------------------------------------------------------


def test_sweep_leaves_newer_rows_intact(conn: sqlite3.Connection) -> None:
    _seed_session(conn)
    base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    _insert_transcript(
        conn,
        row_id="t-old",
        ended_at=_format_ended_at_cutoff(base - timedelta(seconds=120)),
    )
    _insert_transcript(
        conn,
        row_id="t-fresh",
        ended_at=_format_ended_at_cutoff(base - timedelta(seconds=10)),
    )

    deleted = sweep_expired_transcripts(conn, base)

    assert deleted == 1
    survivors = {
        row[0]
        for row in conn.execute("SELECT id FROM transcripts").fetchall()
    }
    assert survivors == {"t-fresh"}


# ---------------------------------------------------------------------
# Case (c) — rows with ended_at IS NULL are untouched
# ---------------------------------------------------------------------


def test_sweep_leaves_in_flight_rows_alone(conn: sqlite3.Connection) -> None:
    """In-flight rows (``ended_at IS NULL``) must survive every sweep
    regardless of how old ``started_at`` is — they're still being
    spoken when the tick fires, and the next tick will see them with a
    populated ``ended_at`` once the utterance ends."""
    _seed_session(conn)
    base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    very_old_started = _format_ended_at_cutoff(base - timedelta(seconds=3600))
    _insert_transcript(
        conn,
        row_id="t-flight",
        ended_at=None,
        started_at=very_old_started,
    )

    deleted = sweep_expired_transcripts(conn, base)

    assert deleted == 0
    row = conn.execute("SELECT id, ended_at FROM transcripts").fetchone()
    assert row[0] == "t-flight"
    assert row[1] is None


# ---------------------------------------------------------------------
# Case (d) — sweep honors current retention setting
# ---------------------------------------------------------------------


def test_sweep_honors_current_retention_setting(conn: sqlite3.Connection) -> None:
    """Bumping retention up keeps a 600s-old row alive; dropping it
    back down makes the next sweep delete that same row.

    Pins the "retention is read fresh per tick" contract — operators
    can flip the preset and have the next 10s sweep honour it without a
    restart.
    """
    _seed_session(conn)
    base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    _insert_transcript(
        conn,
        row_id="t-600s",
        ended_at=_format_ended_at_cutoff(base - timedelta(seconds=600)),
    )

    # Retention bumped to 900s — 600s-old row survives.
    set_retention_seconds(conn, 900)
    deleted_first = sweep_expired_transcripts(conn, base)
    assert deleted_first == 0
    assert conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0] == 1

    # Drop retention to 300s — 600s-old row now past cutoff.
    set_retention_seconds(conn, 300)
    deleted_second = sweep_expired_transcripts(conn, base)
    assert deleted_second == 1
    assert conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0] == 0


# ---------------------------------------------------------------------
# Case (e) — idempotent re-run returns 0
# ---------------------------------------------------------------------


def test_sweep_idempotent_rerun_returns_zero(conn: sqlite3.Connection) -> None:
    """Running the sweep twice in a row with no fresh inserts returns
    zero on the second call — the first run already removed every
    eligible row."""
    _seed_session(conn)
    base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    _insert_transcript(
        conn,
        row_id="t-1",
        ended_at=_format_ended_at_cutoff(base - timedelta(seconds=120)),
    )

    first = sweep_expired_transcripts(conn, base)
    assert first == 1
    second = sweep_expired_transcripts(conn, base)
    assert second == 0


# ---------------------------------------------------------------------
# Case (f) — loop driver catches sweep exceptions + continues
# ---------------------------------------------------------------------


class _FaultyConnection:
    """sqlite3.Connection proxy whose ``execute`` raises on the first call.

    The CPython ``sqlite3.Connection`` slots prevent attribute
    reassignment (``execute`` is read-only), so we wrap the real
    connection in a proxy that intercepts ``execute`` while delegating
    everything else (``commit``, ``close``, attribute access, the
    context-manager protocol).
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ARG002
        raise sqlite3.OperationalError("induced fault for tick 1")

    def commit(self) -> None:
        self._real.commit()

    def close(self) -> None:
        self._real.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _FaultThenRecoverConnFactory:
    """Conn factory whose first call hands out a faulty connection.

    Mirrors the production ``default_worker_conn_factory`` shape — each
    call returns a fresh connection — but the first connection's
    ``execute`` raises ``sqlite3.OperationalError``. Used to prove the
    loop driver's broad ``except Exception`` catches and continues to
    the next tick.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._calls = 0

    def __call__(self) -> sqlite3.Connection:
        self._calls += 1
        real = connect(self._db_path, check_same_thread=False)
        if self._calls == 1:
            return _FaultyConnection(real)  # type: ignore[return-value]
        return real


@pytest.mark.asyncio
async def test_loop_driver_logs_and_continues_after_tick_exception(
    db_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Inject a sqlite3.OperationalError on the first sweep tick.

    The loop driver must:
    * catch the error, log it at ERROR via ``_logger.exception``,
    * NOT propagate to the task — the lifespan stays up,
    * run another tick that succeeds against a healthy conn.
    """
    # Seed a row that the *second* tick will sweep so we can assert
    # forward progress.
    seed_conn = connect(db_path, check_same_thread=False)
    try:
        _seed_session(seed_conn)
        base = datetime.now(UTC)
        _insert_transcript(
            seed_conn,
            row_id="t-old",
            ended_at=_format_ended_at_cutoff(base - timedelta(seconds=600)),
        )
    finally:
        seed_conn.close()

    factory = _FaultThenRecoverConnFactory(db_path)

    # Tight interval + tight overall timeout so we drive a couple of
    # ticks then cancel. ``interval_seconds=0.01`` gives the loop ~50
    # ticks of headroom inside the 0.5s wait_for.
    with caplog.at_level(logging.ERROR, logger="toybox.core.transcript_retention"):
        task = asyncio.create_task(
            run_transcript_sweep_loop(factory, interval_seconds=0.01)
        )
        try:
            # Wait until the bad tick AND a recovery tick both fire.
            # The bad tick is the first conn the factory hands out;
            # subsequent ticks use healthy conns.
            async def _wait_for_recovery() -> None:
                while factory._calls < 2:
                    await asyncio.sleep(0.005)

            await asyncio.wait_for(_wait_for_recovery(), timeout=2.0)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    # The first tick raised; verify it got logged.
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "transcript sweep tick failed" in rec.getMessage() for rec in error_records
    ), f"expected error log; got: {[r.getMessage() for r in error_records]}"

    # The healthy tick(s) deleted the seeded old row — proves forward
    # progress past the fault.
    verify_conn = connect(db_path, check_same_thread=False)
    try:
        remaining = verify_conn.execute(
            "SELECT COUNT(*) FROM transcripts"
        ).fetchone()[0]
    finally:
        verify_conn.close()
    assert remaining == 0


# ---------------------------------------------------------------------
# Case (g) — loop driver tolerates conn_factory() raising
# ---------------------------------------------------------------------


class _FactoryRaisesFirstCall:
    """Conn factory whose first call raises ``sqlite3.OperationalError``.

    Mirrors the production ``default_worker_conn_factory`` shape, but the
    FAULT is in the factory itself — not the connection it hands out.
    Real-world analogues: a transient ``database is locked`` while
    opening the file, a missing DB path during a race with rename, a
    disk-full I/O error on connect.

    Pins the contract that the loop driver guards ``conn_factory()``
    inside the try/except: a factory failure must NOT escape the loop
    and crash the lifespan task.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._calls = 0

    def __call__(self) -> sqlite3.Connection:
        self._calls += 1
        if self._calls == 1:
            raise sqlite3.OperationalError("induced factory fault for tick 1")
        return connect(self._db_path, check_same_thread=False)


@pytest.mark.asyncio
async def test_loop_driver_survives_conn_factory_raising(
    db_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The first ``conn_factory()`` call raises; the loop logs + continues.

    Without the guard around ``conn = conn_factory()``, this exception
    would escape the ``while True`` body, the task would die, and the
    lifespan helper's ``await task`` would re-raise on shutdown — but
    by then ticks have stopped firing and the retention contract is
    broken. The fix pulls the factory call inside the try/except so a
    factory failure is just another bad tick.
    """
    # Seed a row that the *second* (recovery) tick will sweep so we can
    # assert forward progress past the factory fault.
    seed_conn = connect(db_path, check_same_thread=False)
    try:
        _seed_session(seed_conn)
        base = datetime.now(UTC)
        _insert_transcript(
            seed_conn,
            row_id="t-old",
            ended_at=_format_ended_at_cutoff(base - timedelta(seconds=600)),
        )
    finally:
        seed_conn.close()

    factory = _FactoryRaisesFirstCall(db_path)

    with caplog.at_level(logging.ERROR, logger="toybox.core.transcript_retention"):
        task = asyncio.create_task(
            run_transcript_sweep_loop(factory, interval_seconds=0.01)
        )
        try:
            async def _wait_for_recovery() -> None:
                while factory._calls < 2:
                    await asyncio.sleep(0.005)

            await asyncio.wait_for(_wait_for_recovery(), timeout=2.0)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "transcript sweep tick failed" in rec.getMessage() for rec in error_records
    ), f"expected error log; got: {[r.getMessage() for r in error_records]}"

    # The recovery tick swept the seeded old row — proves forward
    # progress past the factory fault.
    verify_conn = connect(db_path, check_same_thread=False)
    try:
        remaining = verify_conn.execute(
            "SELECT COUNT(*) FROM transcripts"
        ).fetchone()[0]
    finally:
        verify_conn.close()
    assert remaining == 0


# ---------------------------------------------------------------------
# Bonus — loop driver propagates CancelledError cleanly on shutdown
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_driver_propagates_cancellation(db_path: Path) -> None:
    """``task.cancel()`` must surface as ``CancelledError`` on ``await
    task`` — the lifespan helper's ``except asyncio.CancelledError: pass``
    only works if the driver re-raises rather than swallowing it via
    the broad ``except Exception``."""

    def _factory() -> sqlite3.Connection:
        return connect(db_path, check_same_thread=False)

    task = asyncio.create_task(
        run_transcript_sweep_loop(_factory, interval_seconds=10.0)
    )
    # Yield once so the loop actually parks in ``asyncio.sleep``.
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
