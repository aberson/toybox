"""Unit coverage for :mod:`toybox.core.proposed_ttl`.

The TTL sweep reaps ``proposed`` rows older than ``3 × cadence`` and
emits one ``activity.state`` envelope per dismissed id. Tests below
drive :func:`sweep_expired_proposed` directly (not through the loop
driver) so the time math is on the test path — the loop's
``asyncio.sleep`` seam is exercised separately by a small end-to-end
test at the bottom of the file.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from toybox.core import play_cadence_seconds, proposed_ttl
from toybox.core.pubsub import PubSub
from toybox.db.connection import connect
from toybox.db.migrations import run_migrations
from toybox.ws.topics import Topic

_TEST_SESSION_ID = "test-ttl-session"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh migrated SQLite file with a seeded test session row."""
    path = tmp_path / "toybox.db"
    conn = connect(path)
    try:
        run_migrations(conn)
        with conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (_TEST_SESSION_ID, "2026-01-01T00:00:00Z"),
            )
    finally:
        conn.close()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """One open connection scoped to the test body."""
    connection = connect(db_path, check_same_thread=False)
    try:
        yield connection
    finally:
        connection.close()


def _iso(ts: datetime) -> str:
    """Render ``ts`` as the canonical UTC-second-Z string.

    Mirrors :func:`toybox.core.proposed_ttl._format_created_at_cutoff`
    exactly so test fixtures use the same shape as production writes
    — otherwise the sweep's lexicographic comparison could match or
    miss depending on whether the test's timestamp uses
    ``+00:00`` vs ``Z`` suffix.
    """
    return ts.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _insert_proposed(
    conn: sqlite3.Connection,
    created_at: str,
) -> str:
    """Insert one ``proposed`` activity row; return its id."""
    activity_id = str(uuid.uuid4())
    summary_blob = json.dumps({"title": "ttl-test", "metadata": {}}, sort_keys=True)
    with conn:
        conn.execute(
            "INSERT INTO activities "
            "(id, session_id, state, version, summary, persona_id, "
            " child_ids, room_ids, toy_ids, intent_source, created_at, "
            " started_at, ended_at) "
            "VALUES (?, ?, 'proposed', 1, ?, NULL, NULL, NULL, NULL, "
            "'ttl-test', ?, NULL, NULL)",
            (activity_id, _TEST_SESSION_ID, summary_blob, created_at),
        )
    return activity_id


def _state_of(conn: sqlite3.Connection, activity_id: str) -> str:
    row = conn.execute(
        "SELECT state FROM activities WHERE id = ?",
        (activity_id,),
    ).fetchone()
    assert row is not None
    return str(row["state"])


def _version_of(conn: sqlite3.Connection, activity_id: str) -> int:
    row = conn.execute(
        "SELECT version FROM activities WHERE id = ?",
        (activity_id,),
    ).fetchone()
    assert row is not None
    return int(row["version"])


def test_sweep_dismisses_expired_proposed_rows(
    conn: sqlite3.Connection,
) -> None:
    """A proposed row older than ``3 × cadence`` is dismissed + envelope emitted.

    Setup: cadence=30s (TTL=90s), row created 100s ago → expired.
    Asserts state flip, version bump, AND that an ``activity.state``
    envelope was published with the dismissed payload (so connected
    UIs see the disappearance).
    """
    play_cadence_seconds.set(conn, 30)
    now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)
    stale_id = _insert_proposed(conn, _iso(now - timedelta(seconds=100)))
    assert _state_of(conn, stale_id) == "proposed"

    pubsub = PubSub()
    sub = pubsub.subscribe([Topic.activity_state])
    try:
        count = proposed_ttl.sweep_expired_proposed(conn, pubsub, now)
        envelope = sub.get_nowait()
    finally:
        sub.close()

    assert count == 1
    assert _state_of(conn, stale_id) == "dismissed"
    # Version bumped from 1 → 2 to match the queue-eviction contract;
    # the parent UI's optimistic-concurrency clients (If-Match-Version
    # gated dismiss/approve) need to see a fresh version or their
    # next mutation will spuriously 409.
    assert _version_of(conn, stale_id) == 2
    assert envelope.topic is Topic.activity_state
    assert envelope.payload["id"] == stale_id
    assert envelope.payload["state"] == "dismissed"


def test_sweep_skips_when_cadence_zero(
    conn: sqlite3.Connection,
) -> None:
    """``cadence == 0`` (disabled) → no-op even with an ancient row.

    The cadence-disabled mode means the parent has opted out of
    autonomous queueing entirely; reaping their pending manual proposes
    would be hostile. The sweep must short-circuit before any UPDATE.
    """
    play_cadence_seconds.set(conn, 0)
    now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)
    # Year-old timestamp — would expire under any non-zero cadence.
    stale_id = _insert_proposed(conn, "2025-05-11T12:00:00Z")

    pubsub = PubSub()
    sub = pubsub.subscribe([Topic.activity_state])
    try:
        count = proposed_ttl.sweep_expired_proposed(conn, pubsub, now)
        # No envelope should have been emitted; subscriber empty.
        assert sub.qsize() == 0
    finally:
        sub.close()

    assert count == 0
    assert _state_of(conn, stale_id) == "proposed"
    assert _version_of(conn, stale_id) == 1


def test_sweep_leaves_unexpired_rows_alone(
    conn: sqlite3.Connection,
) -> None:
    """A row younger than ``3 × cadence`` survives the sweep.

    Setup: cadence=30s (TTL=90s), row created 30s ago → still inside
    the window. The sweep must not touch it. Pins the boundary so a
    regression that flipped the comparison sense (``>`` vs ``<``) or
    used a wrong multiplier (``×1`` instead of ``×3``) would surface.
    """
    play_cadence_seconds.set(conn, 30)
    now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)
    fresh_id = _insert_proposed(conn, _iso(now - timedelta(seconds=30)))

    pubsub = PubSub()
    sub = pubsub.subscribe([Topic.activity_state])
    try:
        count = proposed_ttl.sweep_expired_proposed(conn, pubsub, now)
        assert sub.qsize() == 0
    finally:
        sub.close()

    assert count == 0
    assert _state_of(conn, fresh_id) == "proposed"
    assert _version_of(conn, fresh_id) == 1


def test_sweep_mixes_expired_and_fresh(
    conn: sqlite3.Connection,
) -> None:
    """Mixed sweep: only the expired rows flip; fresh rows are untouched.

    Pins the WHERE clause's per-row filter rather than relying on a
    "sweep everything or nothing" interpretation. One row 100s old
    (expired) and one row 30s old (fresh) under cadence=30s → the
    expired one flips, the fresh one stays.
    """
    play_cadence_seconds.set(conn, 30)
    now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)
    expired_id = _insert_proposed(conn, _iso(now - timedelta(seconds=100)))
    fresh_id = _insert_proposed(conn, _iso(now - timedelta(seconds=30)))

    pubsub = PubSub()
    count = proposed_ttl.sweep_expired_proposed(conn, pubsub, now)

    assert count == 1
    assert _state_of(conn, expired_id) == "dismissed"
    assert _state_of(conn, fresh_id) == "proposed"


def test_sweep_skips_terminal_rows(
    conn: sqlite3.Connection,
) -> None:
    """Already-``dismissed`` rows are not re-touched by the sweep.

    Pins the ``state = 'proposed'`` guard on the UPDATE — a regression
    that dropped the guard would re-emit envelopes for every ancient
    terminal row on every tick, flooding the bus.
    """
    play_cadence_seconds.set(conn, 30)
    now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)
    # Insert as ``proposed`` first (FK + integrity), then flip the
    # state and bump version to simulate an already-dismissed row.
    terminal_id = _insert_proposed(conn, _iso(now - timedelta(seconds=500)))
    with conn:
        conn.execute(
            "UPDATE activities SET state = 'dismissed', version = 2 WHERE id = ?",
            (terminal_id,),
        )

    pubsub = PubSub()
    sub = pubsub.subscribe([Topic.activity_state])
    try:
        count = proposed_ttl.sweep_expired_proposed(conn, pubsub, now)
        assert sub.qsize() == 0
    finally:
        sub.close()

    assert count == 0
    assert _version_of(conn, terminal_id) == 2  # unchanged


# ---------------------------------------------------------------------
# Loop driver: monkeypatched ``asyncio.sleep`` exercises the same
# task pattern as the production lifespan.
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_invokes_sweep_per_tick(
    db_path: Path,
    conn: sqlite3.Connection,
) -> None:
    """The loop calls ``sweep_expired_proposed`` once per sleep cycle.

    Drives the loop with an immediate-resolve sleep so a few ticks
    fire in well under a second, then cancels. Asserts the loop is
    cancellable cleanly (no pending exception) and that the loop
    actually runs the sweep (one stale row → dismissed within a few
    ticks).
    """
    play_cadence_seconds.set(conn, 30)
    # An ancient row that any tick should reap.
    stale_id = _insert_proposed(conn, "2025-01-01T00:00:00Z")

    pubsub = PubSub()
    tick_count = {"n": 0}

    async def _instant_sleep(_seconds: float) -> None:
        tick_count["n"] += 1
        await asyncio.sleep(0)

    task = proposed_ttl.start_proposed_ttl_sweep(
        lambda: pubsub,
        db_path,
        sleep=_instant_sleep,
    )
    try:

        async def _wait_dismissed() -> None:
            while _state_of(conn, stale_id) != "dismissed":
                await asyncio.sleep(0)

        await asyncio.wait_for(_wait_dismissed(), timeout=1.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert _state_of(conn, stale_id) == "dismissed"
    assert tick_count["n"] >= 1


@pytest.mark.asyncio
async def test_loop_survives_sweep_failure(
    db_path: Path,
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sweep exception is logged + the loop continues to the next tick.

    Without the broad-except inside the loop body, a single bad tick
    (locked DB, missing migration, transient sqlite error) would crash
    the task and stop all subsequent ticks. Pin the survival contract.
    """
    play_cadence_seconds.set(conn, 30)
    pubsub = PubSub()
    call_count = {"n": 0}

    real_sweep = proposed_ttl.sweep_expired_proposed

    def _faulty_sweep(
        conn_arg: sqlite3.Connection,
        pubsub_arg: PubSub,
        now: datetime,
    ) -> int:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("induced sweep fault")
        return real_sweep(conn_arg, pubsub_arg, now)

    monkeypatch.setattr(proposed_ttl, "sweep_expired_proposed", _faulty_sweep)

    async def _instant_sleep(_seconds: float) -> None:
        await asyncio.sleep(0)

    task = proposed_ttl.start_proposed_ttl_sweep(
        lambda: pubsub,
        db_path,
        sleep=_instant_sleep,
    )
    try:

        async def _wait_two_calls() -> None:
            while call_count["n"] < 2:
                await asyncio.sleep(0)

        await asyncio.wait_for(_wait_two_calls(), timeout=1.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # First call raised; second call succeeded. Proves the loop body
    # caught and continued.
    assert call_count["n"] >= 2
